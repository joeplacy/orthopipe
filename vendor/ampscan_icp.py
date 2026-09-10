# -*- coding: utf-8 -*-
"""
Minimal vendored ICP rigid-registration core, adapted from ampscan
(``ampscan/align.py`` — the ``align`` class: runICP / linPoint2Plane /
linPoint2Point / getRMSE / getRT).

Upstream is MIT-licensed (see vendor/LICENSE.ampscan) but depends on AmpObject +
VTK. Those dependencies are stripped here so the registration math operates on
plain numpy vertex/face arrays and OrthoPipe need not depend on the dormant
ampscan PyPI package. Only numpy/scipy are imported.

Adapted from ampscan — MIT — https://github.com/abel-research/ampscan
Copyright (c) 2020 Joshua Steer.
"""
from __future__ import annotations
import math
import numpy as np
from scipy import spatial


def _face_centroids(v: np.ndarray, f: np.ndarray) -> np.ndarray:
    return v[f].mean(axis=1)


def _face_normals(v: np.ndarray, f: np.ndarray) -> np.ndarray:
    tris = v[f]
    n = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    ln = np.linalg.norm(n, axis=1, keepdims=True)
    ln[ln == 0] = 1.0
    return n / ln


def linPoint2Plane(mv, sv, sn):
    """Linearised point-to-plane rigid step (small-angle). Returns (R, T).
    Verbatim math from ampscan.align.align.linPoint2Plane."""
    cn = np.c_[np.cross(mv, sn), sn]
    C = np.dot(cn.T, cn)
    v = sv - mv
    b = np.zeros([6])
    for i, col in enumerate(cn.T):
        b[i] = (v * np.repeat(col[:, None], 3, axis=1) * sn).sum()
    X = np.linalg.lstsq(C, b, rcond=None)[0]
    [cx, cy, cz] = np.cos(X[:3])
    [sx, sy, sz] = np.sin(X[:3])
    R = np.array([[cy * cz, sx * sy * cz - cx * sz, cx * sy * cz + sx * sz],
                  [cy * sz, cx * cz + sx * sy * sz, cx * sy * sz - sx * cz],
                  [-sy,                     sx * cy,               cx * cy]])
    T = X[3:]
    return (R, T)


def linPoint2Point(mv, sv):
    """Point-to-point (Kabsch/SVD) rigid step. Returns (R, T).
    Verbatim math from ampscan.align.align.linPoint2Point."""
    mCent = mv - mv.mean(axis=0)
    sCent = sv - sv.mean(axis=0)
    C = np.dot(mCent.T, sCent)
    [U, _, V] = np.linalg.svd(C)
    det = np.linalg.det(np.dot(U, V))
    sign = np.eye(3)
    sign[2, 2] = np.sign(det)
    R = np.dot(V.T, sign)
    R = np.dot(R, U.T)
    T = sv.mean(axis=0) - np.dot(R, mv.mean(axis=0))
    return (R, T)


def register_icp(moving_v, moving_f, static_v, static_f,
                 method="linPoint2Plane", maxiter=20, inlier=1.0):
    """Rigidly register a moving mesh onto a static one via ICP.

    A faithful reduction of ampscan.align.align.runICP: nearest-neighbour to the
    static mesh's face centroids, inlier trimming (``inlier`` = 0-1 proportion of
    closest points kept), and the chosen per-iteration solver (``method`` one of
    "linPoint2Plane" | "linPoint2Point"), for ``maxiter`` iterations.

    Returns ``(T, rmse)``:
      T    -- 4x4 homogeneous transform in trimesh (column-vector) convention,
              i.e. ``new_v = v @ T[:3, :3].T + T[:3, 3]`` — feed straight to
              ``trimesh.Trimesh.apply_transform``.
      rmse -- ampscan's residual: ``sqrt(mean(nearest-neighbour distance))``
              over the retained inliers (see getRMSE upstream).
    """
    mv = np.asarray(moving_v, dtype=float).copy()
    sv = np.asarray(static_v, dtype=float)
    sf = np.asarray(static_f)
    fC = _face_centroids(sv, sf)
    sn = _face_normals(sv, sf)
    kdTree = spatial.cKDTree(fC)

    R_acc = np.eye(3)
    T_acc = np.zeros(3)
    n_inlier = max(1, math.ceil(mv.shape[0] * inlier))

    def _nearest(pts):
        dist, idx = kdTree.query(pts, 1)
        sort = np.argsort(dist)[:n_inlier]
        return dist[sort], idx[sort], sort

    dist, idx, sort = _nearest(mv)
    rmse = math.sqrt(dist.mean())
    for _ in range(maxiter):
        if method == "linPoint2Point":
            R, T = linPoint2Point(mv[sort, :], fC[idx, :])
        elif method == "linPoint2Plane":
            R, T = linPoint2Plane(mv[sort, :], fC[idx, :], sn[idx, :])
        else:
            raise KeyError(f"unsupported ICP method: {method}")
        # accumulate as upstream: R_{i+1}=R.R_i, T_{i+1}=R.T_i+T (row-vector apply)
        R_acc = np.dot(R, R_acc)
        T_acc = np.dot(R, T_acc) + T
        mv = mv @ R.T + T                       # ampscan AmpObject.rigidTransform(R, T)
        dist, idx, sort = _nearest(mv)
        rmse = math.sqrt(dist.mean())

    # re-orthonormalise the accumulated rotation (matches upstream SVD cleanup)
    U, _, V = np.linalg.svd(R_acc)
    R_final = np.dot(U, V)
    Tf = np.eye(4)
    Tf[:3, :3] = R_final
    Tf[:3, 3] = T_acc
    return Tf, float(rmse)
