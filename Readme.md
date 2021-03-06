## Pytorch 1.0 implementation of paper "Patch-base progressive 3D Point Set Upsampling"
This code is a re-implementation of the [original tensorflow code](https://github.com/yifita/3PU) in pytorch 1.0. 
To reproduce results reported in the paper, please use the tensorflow code.

## Complete Demo ##
Make sure cuda, cudnn, nccl is correctly installed.
We tested with cuda 9.2 and cudnn 7.4.1 but cuda 9.0+ should work.

```bash
# clone
git clone https://github.com/yifita/3PU_pytorch
cd 3PU_pytorch
# download pretrained models
curl -o pytorch_converted_models.zip -O {https://polybox.ethz.ch/index.php/s/QsnhBDg17bX8alE/download}
unzip pytorch_converted_models.zip
# download test data
curl -o data/test_data/test_points.zip -O https://polybox.ethz.ch/index.php/s/wxKg4O05JnyePDK/download
unzip -d data/test_data/ data/test_data/test_points.zip

# conda environment
conda env create -f environment.yml
conda activate pytorch-1.0

# compile
cd losses
python setup.py install
cd ../sampling
python setup.py install

# run code
python main.py --phase test --num_point 312 --num_shape_point 5000 --up_ratio 16 --test_data  "data/test_data/sketchfab_poisson/poisson_5000/*.xyz" --ckpt final_poisson.pth
``` 
## data preparation ##
We use h5 file for training, download it [here](https://polybox.ethz.ch/index.php/s/o3wzIrKlsngtwsb).
To create input point clouds from meshes, please follow [this](https://github.com/yifita/3PU#data-preparation) instruction.

We converted the pretrained models described [here](https://github.com/yifita/3PU#pretrained-model) to pytorch. Download them [here](https://polybox.ethz.ch/index.php/s/QsnhBDg17bX8alE). This file contains two pytorch binaries `final_poisson.pth` and `final_scan.pth`, unzip them 

## compile ##

0. Install cuda, cudnn and nccl if you haven't done so yet.
1. Create conda environment `conda env create -f enviroment.yml` with the prepared `enviroment.yml`. This will create a conda environment named "pytorch-1.0".
2. Compile pytorch extension
    ````bash
    conda activate pytorch-1.0
    cd losses
    python setup.py install
    cd ../sampling
    python setup.py install
    ````

## execution ##

### testing ###
```bash
# 16x superresolution and save the results under "./model/poisson"
python main.py --phase test --id "poisson" --num_point 312 --num_shape_point 5000 --up_ratio 16 --test_data  "data/test_data/sketchfab_poisson/poisson_5000/*.xyz" --ckpt ./model/final_poisson.pth 
```
### training ###
Download training data from [here](https://polybox.ethz.ch/index.php/s/o3wzIrKlsngtwsb).

The training code uses visdom for visualization. Start visdom and go to `http://localhost:8097` in browser to see monitor training progress.
```bash
conda activate pytorch-1.0
python -m visdom.server
```
Then run the training command in another terminal
```bash
# training using default settings
conda activate pytorch-1.0

python main.py --phase train --id "demo_train" --num_point 312 --num_shape_point 5000 --up_ratio 16 --h5_data train_poisson_310_poisson_625_poisson_1250_poisson_2500_poisson_5000_poisson_10000_poisson_20000_poisson_40000_poisson_80000.hdf5
```

## cite ##
```
@InProceedings{Yifan_2019_CVPR,
author = {Yifan, Wang and Wu, Shihao and Huang, Hui and Cohen-Or, Daniel and Sorkine-Hornung, Olga},
title = {Patch-Based Progressive 3D Point Set Upsampling},
booktitle = {The IEEE Conference on Computer Vision and Pattern Recognition (CVPR)},
month = {June},
year = {2019}
}

```
## acknowledgement ##
This code based is created cortesy of [erikwijmans](https://github.com/erikwijmans/Pointnet2_PyTorch), [charlesq34](https://github.com/charlesq34/pointnet2) and [yangyan](https://github.com/yangyanli/PointCNN).

## contact ##
Yifan Wang: yifan.wang@inf.ethz.ch
