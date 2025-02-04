#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ---------------------------------------------------------------------------
# Copyright 2023 Diamond Light Source Ltd.
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
# Created Date: 23 March 2023
# ---------------------------------------------------------------------------
"""Module for data type morphing functions"""

import cupy as cp 
import numpy as np
import nvtx
from typing import Literal, Tuple
from httomolibgpu.decorator import method_sino

__all__ = [
    "sino_360_to_180",
]


def _calc_max_slices_sino_360_to_180(
    other_dims: Tuple[int, int], dtype: np.dtype, available_memory: int, **kwargs
) -> Tuple[int, np.dtype]:
    assert 'overlap' in kwargs, "Overlap not given"
    overlap = int(np.round(kwargs['overlap']))
    in_slice = np.prod(other_dims) * dtype.itemsize
    out_slice = other_dims[0] * (other_dims[1] * 2 - overlap) / 2 * dtype.itemsize
    # we have to leave this as 64bit to match tomopy?
    weights = overlap * np.float64().nbytes

    available_memory -= weights
    return int(np.floor(available_memory / (in_slice + out_slice))), dtype



@method_sino(_calc_max_slices_sino_360_to_180)
@nvtx.annotate()
def sino_360_to_180(
    data: cp.ndarray, overlap: int = 0, rotation: Literal["left", "right"] = "left"
) -> cp.ndarray:
    """
    Converts 0-360 degrees sinogram to a 0-180 sinogram.
    If the number of projections in the input data is odd, the last projection
    will be discarded.

    Parameters
    ----------
    data : cp.ndarray
        Input 3D data.
    overlap : scalar, optional
        Overlapping number of pixels.
    rotation : string, optional
        'left' if rotation center is close to the left of the
        field-of-view, 'right' otherwise.
    Returns
    -------
    cp.ndarray
        Output 3D data.
    """
    if data.ndim != 3:
        raise ValueError("only 3D data is supported")

    dx, dy, dz = data.shape

    overlap = int(np.round(overlap))
    if overlap >= dz:
        raise ValueError("overlap must be less than data.shape[2]")
    if overlap < 0:
        raise ValueError("only positive overlaps are allowed.")

    n = dx // 2

    out = cp.empty((n, dy, 2 * dz - overlap), dtype=data.dtype)

    if rotation == "left":
        weights = cp.linspace(0, 1.0, overlap)
        out[:, :, -dz + overlap :] = data[:n, :, overlap:]
        out[:, :, : dz - overlap] = data[n : 2 * n, :, overlap:][:, :, ::-1]
        out[:, :, dz - overlap : dz] = (
            weights * data[:n, :, :overlap]
            + (weights * data[n : 2 * n, :, :overlap])[:, :, ::-1]
        )
    elif rotation == "right":
        weights = cp.linspace(1.0, 0, overlap)
        out[:, :, : dz - overlap] = data[:n, :, :-overlap]
        out[:, :, -dz + overlap :] = data[n : 2 * n, :, :-overlap][:, :, ::-1]
        out[:, :, dz - overlap : dz] = (
            weights * data[:n, :, -overlap:]
            + (weights * data[n : 2 * n, :, -overlap:])[:, :, ::-1]
        )
    else:
        raise ValueError('rotation parameter must be either "left" or "right"')

    return out
