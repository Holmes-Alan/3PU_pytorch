import torch
import faiss

import sampling

from faiss_setup import GPU_RES


def normalize_point_batch(pc, NCHW=True):
    """
    normalize a batch of point clouds
    :param
        pc      [B, N, 3] or [B, 3, N]
        NCHW    if True, treat the second dimension as channel dimension
    :return
        pc      normalized point clouds, same shape as input
        centroid [B, 1, 3] or [B, 3, 1] center of point clouds
        furthest_distance [B, 1, 1] scale of point clouds
    """
    point_axis = 2 if NCHW else 1
    dim_axis = 1 if NCHW else 2
    centroid = torch.mean(pc, dim=point_axis, keepdim=True)
    pc = pc - centroid
    furthest_distance, _ = torch.max(
        torch.sqrt(torch.sum(pc ** 2, dim=dim_axis, keepdim=True)), dim=point_axis, keepdim=True)
    pc = pc / furthest_distance
    return pc, centroid, furthest_distance


def search_index_pytorch(database, x, k, D=None, I=None):
    """
    KNN search via Faiss
    :param
        database BxNxC
        x BxMxC
    :return
        D BxMxK
        I BxMxK
    """
    Dptr = database.storage().data_ptr()
    index = faiss.GpuIndexFlatL2(GPU_RES, database.size(-1))  # dimension is 3
    index.add_c(database.size(0), faiss.cast_integer_to_float_ptr(Dptr))

    assert x.is_contiguous()
    n, d = x.size()
    assert d == index.d

    if D is None:
        if x.is_cuda:
            D = torch.cuda.FloatTensor(n, k)
        else:
            D = torch.FloatTensor(n, k)
    else:
        assert D.__class__ in (torch.FloatTensor, torch.cuda.FloatTensor)
        assert D.size() == (n, k)
        assert D.is_contiguous()

    if I is None:
        if x.is_cuda:
            I = torch.cuda.LongTensor(n, k)
        else:
            I = torch.LongTensor(n, k)
    else:
        assert I.__class__ in (torch.LongTensor, torch.cuda.LongTensor)
        assert I.size() == (n, k)
        assert I.is_contiguous()
    torch.cuda.synchronize()
    xptr = x.storage().data_ptr()
    Iptr = I.storage().data_ptr()
    Dptr = D.storage().data_ptr()
    index.search_c(n, faiss.cast_integer_to_float_ptr(xptr),
                   k, faiss.cast_integer_to_float_ptr(Dptr),
                   faiss.cast_integer_to_long_ptr(Iptr))
    torch.cuda.synchronize()
    index.reset()
    return D, I


class KNN(torch.autograd.Function):
    @staticmethod
    def forward(ctx, k, query, points):
        """
        :param k: k in KNN
               query: BxMxC
               points: BxNxC
        :return:
            neighbors_points: BxMxK
            index_batch: BxMxK
        """
        # selected_gt: BxkxCxM
        # process each batch independently.
        index_batch = []
        distance_batch = []
        for i in range(points.shape[0]):
            D_var, I_var = search_index_pytorch(points[i], query[i], k)
            GPU_RES.syncDefaultStreamCurrentDevice()
            index_batch.append(I_var)  # M, k
            distance_batch.append(D_var)  # M, k

        # B, M, K
        index_batch = torch.stack(index_batch, dim=0)
        distance_batch = torch.stack(distance_batch, dim=0)
        ctx.mark_non_differentiable(index_batch, distance_batch)
        return index_batch, distance_batch


def group_knn(k, query, points, unique=True, NCHW=True):
    """
    group batch of points to neighborhoods
    :param
        k: neighborhood size
        query: BxCxM or BxMxC
        points: BxCxN or BxNxC
        unique: neighborhood contains *unique* points
        NCHW: if true, the second dimension is the channel dimension
    :return
        neighbor_points BxCxMxk (if NCHW) or BxMxkxC (otherwise)
        index_batch     BxMxk
        distance_batch  BxMxk
    """
    if NCHW:
        batch_size, channels, num_points = points.size()
        points_trans = points.transpose(2, 1).contiguous()
        query_trans = query.transpose(2, 1).contiguous()
    else:
        points_trans = points.contiguous()
        query_trans = query.contiguous()

    batch_size, num_points, _ = points_trans.size()
    assert(num_points >= query.size(1)
           ), "points size must be greater or equal to query size"
    # BxMxk
    index_batch, distance_batch = KNN.apply(k, query_trans, points_trans)
    # BxNxC -> BxMxNxC
    points_expanded = points_trans.unsqueeze(dim=1).expand(
        (-1, query.size(2), -1, -1))
    # BxMxk -> BxMxkxC
    index_batch_expanded = index_batch.unsqueeze(dim=-1).expand(
        (-1, -1, -1, points_trans.size(-1)))
    # BxMxkxC
    neighbor_points = torch.gather(points_expanded, 2, index_batch_expanded)
    index_batch = index_batch
    if NCHW:
        # BxCxMxk
        neighbor_points = neighbor_points.permute(0, 3, 1, 2).contiguous()
    return neighbor_points, index_batch, distance_batch


class GatherFunction(torch.autograd.Function):
    @staticmethod
    def forward(ctx, features, idx):
        r"""
        Parameters
        ----------
        features : torch.Tensor
            (B, C, N) tensor
        idx : torch.Tensor
            (B, npoint) tensor of the features to gather
        Returns
        -------
        torch.Tensor
            (B, C, npoint) tensor
        """
        features = features.contiguous()
        idx = idx.contiguous()

        B, npoint = idx.size()
        _, C, N = features.size()

        output = torch.empty(
            B, C, npoint, dtype=features.dtype, device=features.device)
        output = sampling.gather_forward(
            B, C, N, npoint, features, idx, output
        )

        ctx.save_for_backward(idx)
        ctx.C = C
        ctx.N = N
        return output

    @staticmethod
    def backward(ctx, grad_out):
        idx, = ctx.saved_tensors
        B, npoint = idx.size()

        grad_features = torch.zeros(
            B, ctx.C, ctx.N, dtype=grad_out.dtype, device=grad_out.device)
        grad_features = sampling.gather_backward(
            B, ctx.C, ctx.N, npoint, grad_out.contiguous(), idx, grad_features
        )

        return grad_features, None


gather_points = GatherFunction.apply


class FurthestPointSampling(torch.autograd.Function):

    @staticmethod
    def forward(ctx, xyz, npoint):
        r"""
        Uses iterative furthest point sampling to select a set of npoint features that have the largest
        minimum distance
        Parameters
        ----------
        xyz : torch.Tensor
            (B, N, 3) tensor where N > npoint
        npoint : int32
            number of features in the sampled set
        Returns
        -------
        torch.LongTensor
            (B, npoint) tensor containing the indices

        """
        B, N, _ = xyz.size()

        idx = torch.empty([B, npoint], dtype=torch.int32, device=xyz.device)
        temp = torch.full([B, N], 1e10, dtype=torch.float32, device=xyz.device)

        sampling.furthest_sampling(
            B, N, npoint, xyz, temp, idx
        )
        ctx.mark_non_differentiable(idx)
        return idx


__furthest_point_sample = FurthestPointSampling.apply


def furthest_point_sample(xyz, npoint, NCHW=True):
    """
    :param
        xyz (B, 3, N) or (B, N, 3)
        npoint a constant
    :return
        torch.LongTensor
            (B, npoint) tensor containing the indices
        torch.FloatTensor
            (B, npoint, 3) or (B, 3, npoint) point sets"""
    assert(xyz.dim() == 3), "input for furthest sampling must be a 3D-tensor, but xyz.size() is {}".format(xyz.size())
    # need transpose
    if NCHW:
        xyz = xyz.transpose(2, 1).contiguous()

    assert(xyz.size(2) == 3), "furthest sampling is implemented for 3D points"
    idx = __furthest_point_sample(xyz, npoint)
    sampled_pc = gather_points(xyz.transpose(2, 1).contiguous(), idx)
    if not NCHW:
        sampled_pc = sampled_pc.transpose(2, 1).contiguous()
    return idx, sampled_pc


# class FurthestPoint(torch.nn.Module):
#     """
#     Furthest point sampling for Bx3xN points
#     param:
#         xyz: Bx3XN or BxNx3 tensor
#         npoint: number of points
#     return:
#         idx: Bxnpoint indices
#         sampled_xyz: Bx3xnpoint coordinates
#     """
#     def forward(self, xyz, npoint):
#         assert(xyz.dim() == 3), "input for furthest sampling must be a 3D-tensor, but xyz.size() is {}".format(xyz.size())
#         # need transpose
#         if xyz.size(2) != 3:
#             assert(xyz.size(1) == 3), "furthest sampling is implemented for 3D points"
#             xyz = xyz.transpose(2, 1).contiguous()

#         assert(xyz.size(2) == 3), "furthest sampling is implemented for 3D points"
#         idx = furthest_point_sample(xyz, npoint)
#         sampled_pc = gather_points(xyz.transpose(2, 1).contiguous(), idx)
#         return idx, sampled_pc


if __name__ == '__main__':
    from utils.pc_utils import read_ply, save_ply, save_ply_property
    cuda0 = torch.device('cuda:0')
    pc = read_ply("/home/ywang/Documents/points/point-upsampling/3PU/prepare_data/polygonmesh_base/build/data_PPU_output/training/112/angel4_aligned_2.ply")
    pc = pc[:, :3]
    print("{} input points".format(pc.shape[0]))
    save_ply(pc, "./input.ply", colors=None, normals=None)
    pc = torch.from_numpy(pc).requires_grad_().to(cuda0).unsqueeze(0)
    pc = pc.transpose(2, 1)

    # test furthest point
    idx, sampled_pc = furthest_point_sample(pc, 1250)
    output = sampled_pc.transpose(2, 1).cpu().squeeze()
    save_ply(output.detach(), "./output.ply", colors=None, normals=None)

    # test KNN
    knn_points, _, _ = group_knn(10, sampled_pc, pc, NCHW=True)  # B, C, M, K
    labels = torch.arange(0, knn_points.size(2)).unsqueeze_(
        0).unsqueeze_(0).unsqueeze_(-1)  # 1, 1, M, 1
    labels = labels.expand(knn_points.size(0), -1, -1,
                           knn_points.size(3))  # B, 1, M, K
    # B, C, P
    labels = torch.cat(torch.unbind(labels, dim=-1), dim=-
                       1).squeeze().detach().cpu().numpy()
    knn_points = torch.cat(torch.unbind(knn_points, dim=-1),
                           dim=-1).transpose(2, 1).squeeze(0).detach().cpu().numpy()
    save_ply_property(knn_points, labels, "./knn_output.ply", cmap_name='jet')

    from torch.autograd import gradcheck
    # test = gradcheck(furthest_point_sample, [pc, 1250], eps=1e-6, atol=1e-4)
    # print(test)
    test = gradcheck(gather_points, [pc.to(
        dtype=torch.float64), idx], eps=1e-6, atol=1e-4)
    print(test)
