#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ---------------------------------------------------------------------------
# Copyright 2022 Diamond Light Source Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ---------------------------------------------------------------------------
# Created By  : Tomography Team at DLS <scientificsoftware@diamond.ac.uk>
# Created Date: 01 November 2022
# ---------------------------------------------------------------------------
"""Module for tomographic reconstruction"""

from typing import Optional, Tuple, Union

import cupy as cp
from cupy import float32, complex64
import cupyx
import numpy as np
import nvtx
from httomolibgpu.decorator import method_sino

from httomolibgpu.cuda_kernels import load_cuda_module

__all__ = [
    "FBP",
    "SIRT",
    "CGLS",
]


def _calc_max_slices_FBP(
    non_slice_dims_shape: Tuple[int, int],
    dtype: np.dtype,
    available_memory: int,
    **kwargs
) -> Tuple[int, np.dtype, Tuple[int, int]]:
    # we first run filtersync, and calc the memory for that
    DetectorsLengthH = non_slice_dims_shape[1]
    in_slice_size = np.prod(non_slice_dims_shape) * dtype.itemsize
    filter_size = (DetectorsLengthH//2+1) * float32().itemsize
    freq_slice = non_slice_dims_shape[0] * (DetectorsLengthH//2+1) * complex64().itemsize
    fftplan_size = freq_slice * 2
    filtered_in_data = np.prod(non_slice_dims_shape) * float32().itemsize
    # calculate the output shape
    objsize = kwargs['objsize']
    if objsize is None:
        objsize = DetectorsLengthH
    output_dims = (objsize, objsize)
    # astra backprojection will generate an output array 
    astra_out_size = (np.prod(output_dims) * float32().itemsize)

    available_memory -= filter_size
    slices_max = available_memory // int(in_slice_size + filtered_in_data + freq_slice + fftplan_size + astra_out_size)
    return (slices_max, float32(), output_dims)


## %%%%%%%%%%%%%%%%%%%%%%% FBP reconstruction %%%%%%%%%%%%%%%%%%%%%%%%%%%%  ##
@method_sino(_calc_max_slices_FBP)
@nvtx.annotate()
def FBP(
    data: cp.ndarray,
    angles: np.ndarray,
    center: Optional[float] = None,
    objsize: Optional[int] = None,
    gpu_id: int = 0,
) -> cp.ndarray:
    """
    Perform Filtered Backprojection (FBP) reconstruction using ASTRA toolbox and ToMoBAR wrappers.
    This is 3D recon from a CuPy array and a custom built filter.

    Parameters
    ----------
    data : cp.ndarray
        Projection data as a CuPy array.
    angles : np.ndarray
        An array of angles given in radians.
    center : float, optional
        The center of rotation (CoR).
    objsize : int, optional
        The size in pixels of the reconstructed object.
    gpu_id : int, optional
        A GPU device index to perform operation on.

    Returns
    -------
    cp.ndarray
        The FBP reconstructed volume as a CuPy array.
    """
    from tomobar.methodsDIR_CuPy import RecToolsDIRCuPy

    if center is None:
        center = data.shape[2] // 2  # making a crude guess
    if objsize is None:
        objsize = data.shape[2]
        
    RecToolsCP = RecToolsDIRCuPy(DetectorsDimH=data.shape[2],  # Horizontal detector dimension
                                 DetectorsDimV=data.shape[1],  # Vertical detector dimension (3D case)
                                 CenterRotOffset=data.shape[2] / 2 - center - 0.5,  # Center of Rotation scalar or a vector
                                 AnglesVec=-angles,  # A vector of projection angles in radians
                                 ObjSize=objsize,  # Reconstructed object dimensions (scalar)
                                 device_projector=gpu_id,
                                 )
    reconstruction = RecToolsCP.FBP3D(data)
    cp._default_memory_pool.free_all_blocks()
    return reconstruction

## %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%  ##

def _calc_max_slices_SIRT(
    non_slice_dims_shape: Tuple[int, int],
    dtype: np.dtype,
    available_memory: int, **kwargs
) -> Tuple[int, np.dtype, Tuple[int, int]]:
    # calculate the output shape
    DetectorsLengthH = non_slice_dims_shape[1]
    objsize = kwargs['objsize']
    if objsize is None:
        objsize = DetectorsLengthH
    output_dims = (objsize, objsize) 
    
    # input/output
    data_out = np.prod(non_slice_dims_shape) * dtype.itemsize
    x_rec = np.prod(output_dims) * dtype.itemsize
    # preconditioning matrices R and C
    R_mat = data_out
    C_mat = x_rec
    # update_term
    C_R_res = C_mat + 2*R_mat
    # a guess for astra toolbox memory usage for projection/backprojection
    astra_size = 0.5*(x_rec+data_out)
   
    total_mem = int(data_out + x_rec + R_mat + C_mat + C_R_res + astra_size)
    slices_max = available_memory // total_mem
    return (slices_max, float32(), output_dims)


## %%%%%%%%%%%%%%%%%%%%%%% SIRT reconstruction %%%%%%%%%%%%%%%%%%%%%%%%%%%%  ##
@method_sino(_calc_max_slices_SIRT)
@nvtx.annotate()
def SIRT(
    data: cp.ndarray,
    angles: np.ndarray,
    center: Optional[float] = None,
    objsize: Optional[int] = None,
    iterations: Optional[int] = 300,
    nonnegativity: Optional[bool] = True,
    gpu_id: int = 0,
) -> cp.ndarray:
    """
    Perform Simultaneous Iterative Recostruction Technique (SIRT) using ASTRA toolbox and ToMoBAR wrappers.
    This is 3D recon directly from a CuPy array while using ASTRA GPUlink capability.

    Parameters
    ----------
    data : cp.ndarray
        Projection data as a CuPy array.
    angles : np.ndarray
        An array of angles given in radians.
    center : float, optional
        The center of rotation (CoR).
    objsize : int, optional
        The size in pixels of the reconstructed object.
    iterations : int, optional
        The number of SIRT iterations.
    nonnegativity : bool, optional
        Impose nonnegativity constraint on reconstructed image.
    gpu_id : int, optional
        A GPU device index to perform operation on.

    Returns
    -------
    cp.ndarray
        The SIRT reconstructed volume as a CuPy array.
    """
    from tomobar.methodsIR_CuPy import RecToolsIRCuPy

    if center is None:
        center = data.shape[2] // 2  # making a crude guess
    if objsize is None:
        objsize = data.shape[2]
        
    RecToolsCP = RecToolsIRCuPy(DetectorsDimH=data.shape[2],  # Horizontal detector dimension
                                 DetectorsDimV=data.shape[1],  # Vertical detector dimension (3D case)
                                 CenterRotOffset=data.shape[2] / 2 - center - 0.5,  # Center of Rotation scalar or a vector
                                 AnglesVec=-angles,  # A vector of projection angles in radians
                                 ObjSize=objsize,  # Reconstructed object dimensions (scalar)
                                 device_projector=gpu_id,
                                 )
    _data_ = {"projection_norm_data": data}  # data dictionary
    _algorithm_ = {"iterations": iterations, "nonnegativity": nonnegativity}
    reconstruction = RecToolsCP.SIRT(_data_, _algorithm_)
    cp._default_memory_pool.free_all_blocks()
    return reconstruction

## %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%  ##
def _calc_max_slices_CGLS(
    non_slice_dims_shape: Tuple[int, int],
    dtype: np.dtype,
    available_memory: int, **kwargs
) -> Tuple[int, np.dtype, Tuple[int, int]]:
    # calculate the output shape
    DetectorsLengthH = non_slice_dims_shape[1]
    objsize = kwargs['objsize']
    if objsize is None:
        objsize = DetectorsLengthH
    output_dims = (objsize, objsize) 
    
    # input/output
    data_out = np.prod(non_slice_dims_shape) * dtype.itemsize
    x_rec = np.prod(output_dims) * dtype.itemsize
    # d and r vectors    
    d = x_rec
    r = data_out
    Ad = 2*data_out
    s = x_rec
    # a guess for astra toolbox memory usage for projection/backprojection
    astra_size = 0.5*(x_rec+data_out)
   
    total_mem = int(data_out + x_rec + d + r + Ad + s + astra_size)
    slices_max = available_memory // total_mem
    return (slices_max, float32(), output_dims)

## %%%%%%%%%%%%%%%%%%%%%%% CGLS reconstruction %%%%%%%%%%%%%%%%%%%%%%%%%%%%  ##
@method_sino(_calc_max_slices_CGLS)
@nvtx.annotate()
def CGLS(
    data: cp.ndarray,
    angles: np.ndarray,
    center: Optional[float] = None,
    objsize: Optional[int] = None,
    iterations: Optional[int] = 20,
    nonnegativity: Optional[bool] = True,
    gpu_id: int = 0,
) -> cp.ndarray:
    """
    Perform Congugate Gradient Least Squares (CGLS) using ASTRA toolbox and ToMoBAR wrappers.
    This is 3D recon directly from a CuPy array while using ASTRA GPUlink capability.

    Parameters
    ----------
    data : cp.ndarray
        Projection data as a CuPy array.
    angles : np.ndarray
        An array of angles given in radians.
    center : float, optional
        The center of rotation (CoR).
    objsize : int, optional
        The size in pixels of the reconstructed object.
    iterations : int, optional
        The number of CGLS iterations.
    nonnegativity : bool, optional
        Impose nonnegativity constraint on reconstructed image.
    gpu_id : int, optional
        A GPU device index to perform operation on.

    Returns
    -------
    cp.ndarray
        The CGLS reconstructed volume as a CuPy array.
    """
    from tomobar.methodsIR_CuPy import RecToolsIRCuPy

    if center is None:
        center = data.shape[2] // 2  # making a crude guess
    if objsize is None:
        objsize = data.shape[2]
        
    RecToolsCP = RecToolsIRCuPy(DetectorsDimH=data.shape[2],  # Horizontal detector dimension
                                 DetectorsDimV=data.shape[1],  # Vertical detector dimension (3D case)
                                 CenterRotOffset=data.shape[2] / 2 - center - 0.5,  # Center of Rotation scalar or a vector
                                 AnglesVec=-angles,  # A vector of projection angles in radians
                                 ObjSize=objsize,  # Reconstructed object dimensions (scalar)
                                 device_projector=gpu_id,
                                 )
    _data_ = {"projection_norm_data": data}  # data dictionary
    _algorithm_ = {"iterations": iterations, "nonnegativity": nonnegativity}
    reconstruction = RecToolsCP.CGLS(_data_, _algorithm_)
    cp._default_memory_pool.free_all_blocks()
    return reconstruction

## %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%  ##