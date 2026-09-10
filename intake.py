"""
intake.py — phone-scan intake helpers (batch-2 item 6).

Normalizes a freshly-uploaded scan for the existing `/api/upload` -> `/api/generate`
flow: converts `.usdz` (TrueDepth/LiDAR export) to `.obj` when USD tooling is
present, and fixes meters-vs-mm scale. Small and standalone — no geometry-engine
coupling; `generate_orthotic` is unchanged and still just `trimesh.load`s a path.

`.obj/.stl/.ply` already load natively in trimesh, so they pass through untouched
(aside from the meters heuristic). `.usdz` needs `usd-core`, which is optional and
lazy-imported: absent -> a clean `ValueError` the endpoint maps to HTTP 400.
"""
from __future__ import annotations
from pathlib import Path

import numpy as np
import trimesh

# validate()'s length band is 120-360 mm (geometry.py); anchor the scale heuristic to it.
MIN_PLAUSIBLE_FOOT_MM = 40.0


def check_and_fix_scale(mesh: trimesh.Trimesh) -> tuple[trimesh.Trimesh, list[str]]:
    """Fix meters-vs-mm scale. Phone captures are sometimes in meters (~0.12-0.36).
    Returns (mesh, warnings). Only auto-scales when clearly sub-mm-scale."""
    warnings: list[str] = []
    long = float(max(mesh.extents))
    if long < 1.0:                      # a real foot is never < 1 unit if mm
        mesh.apply_scale(1000.0)        # meters -> mm
        warnings.append("scan appears to be in meters; auto-scaled ×1000 to mm")
    elif long < MIN_PLAUSIBLE_FOOT_MM:  # ambiguous small — warn, don't touch
        warnings.append(f"scan long-axis {long:.1f} — below plausible foot length; check units")
    return mesh, warnings


def _convert_usdz(path: str) -> tuple[str, list[str]]:
    """Convert a .usdz mesh export to .obj via usd-core. Raises ValueError (mapped to
    HTTP 400) if USD tooling is absent — never hard-crashes the server."""
    try:
        from pxr import Usd, UsdGeom      # optional, lazy
    except ImportError as e:
        raise ValueError("usdz upload received but USD tooling not installed — "
                         "export .obj/.ply from the capture app") from e
    stage = Usd.Stage.Open(path)
    if stage is None:
        raise ValueError("could not open usdz stage")
    verts: list = []
    faces: list = []
    for prim in stage.Traverse():
        if not prim.IsA(UsdGeom.Mesh):
            continue
        m = UsdGeom.Mesh(prim)
        pts = np.asarray(m.GetPointsAttr().Get(), dtype=float)
        counts = np.asarray(m.GetFaceVertexCountsAttr().Get(), dtype=int)
        idx = np.asarray(m.GetFaceVertexIndicesAttr().Get(), dtype=int)
        base = len(verts)
        off = 0
        for c in counts:                 # fan-triangulate quads/ngons
            poly = idx[off:off + c]
            off += c
            for k in range(1, c - 1):
                faces.append([base + int(poly[0]), base + int(poly[k]), base + int(poly[k + 1])])
        verts.extend(pts.tolist())
    if not verts or not faces:
        raise ValueError("no mesh geometry found in usdz")
    out = str(Path(path).with_suffix(".obj"))
    trimesh.Trimesh(vertices=np.asarray(verts), faces=np.asarray(faces)).export(out)
    return out, ["converted usdz -> obj"]


def normalize_scan(path: str) -> tuple[str, list[str]]:
    """Return (loadable_path, warnings) for an uploaded scan.

    .obj/.stl/.ply pass through (re-exported only if the meters heuristic fires);
    .usdz is converted to .obj. Scale is checked/fixed on the loaded geometry and,
    if corrected, re-exported so the heightfield is built at mm scale. Geometry only
    — mesh metadata is never propagated (de-id)."""
    warnings: list[str] = []
    ext = Path(path).suffix.lower()

    if ext == ".usdz":
        path, cw = _convert_usdz(path)
        warnings += cw
        ext = ".obj"

    if ext not in (".obj", ".stl", ".ply"):
        raise ValueError(f"unsupported scan format: {ext}")

    mesh = trimesh.load(path, force="mesh")     # force="mesh": drop scene/metadata
    if hasattr(mesh, "metadata"):
        mesh.metadata.clear()                    # de-id: never propagate metadata
    mesh, sw = check_and_fix_scale(mesh)
    warnings += sw

    if any("auto-scaled" in w for w in sw):     # only re-export when we changed it
        corrected = str(Path(path).with_name(Path(path).stem + "_mm.obj"))
        mesh.export(corrected)
        return corrected, warnings
    return path, warnings
