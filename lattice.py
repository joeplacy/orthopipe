"""
Variable-stiffness lattice interior (OrthoPipe item 4).

Replaces the fully-solid orthotic interior with a watertight, printable TPMS
lattice whose local volume fraction follows the per-zone density field carried by
`FootRx.fill` (schema.py). Solid fill stays the default and is the 100%-density
limit of the same code path — `apply_lattice` is a true no-op when no lattice is
requested.

Pipeline (side-effect-free, importable):
  voxelize the finished solid -> per-zone density/family field (blended along the
  heel->toe axis) -> evaluate the TPMS implicit field per voxel -> threshold to a
  spatially-varying wall calibrated so occupied-fraction == prescribed density ->
  keep a solid skin band of MIN_THICKNESS at every boundary -> marching cubes ->
  intersect with the original solid (manifold3d) so the outer surface and floor
  come from `solid` exactly and the result is watertight and <= the solid volume.

Self-contained: only numpy / scipy / skimage / trimesh / manifold3d. No GPL
(microgen etc.) — deliberately avoided as a copyleft trap for a distributed
STL-generating product.
"""
from __future__ import annotations
import numpy as np
import trimesh
from scipy import ndimage
from skimage import measure
import manifold3d as m3d

from schema import FootRx

# --- tunables ---------------------------------------------------------------
LATTICE_PITCH_MM = 0.8      # voxel pitch; ~CELL_MM/2, finer to resolve thin walls
UNIT_CELL_MM = 10.0         # TPMS period L (spec range 6-10mm)
MIN_THICKNESS_MM = 2.0      # solid skin band at every boundary (mirrors geometry)
ZONE_BLEND_SIGMA = 0.15     # gaussian blend width in normalized length (smoothness)

# The schema's Zone names are anatomical bands, NOT landmark names; hf.landmark()
# only accepts the five landmarks below, so map each zone to a concrete landmark.
ZONE_TO_LANDMARK = {
    "heel":     "heel_center",
    "forefoot": "met_heads",
    "toe":      "hallux",
    "midfoot":  "arch_apex",   # midfoot has no point landmark -> arch_apex row (a band)
}
ZONES = ["heel", "midfoot", "forefoot", "toe"]
FAMILIES = ("solid", "gyroid", "diamond", "primitive")


# --- TPMS implicit fields ---------------------------------------------------

def _tpms(family: str, x, y, z):
    """Triply-periodic minimal-surface implicit field F(x,y,z), args pre-scaled by k."""
    if family == "gyroid":
        return np.sin(x) * np.cos(y) + np.sin(y) * np.cos(z) + np.sin(z) * np.cos(x)
    if family == "primitive":                      # Schwarz Primitive
        return np.cos(x) + np.cos(y) + np.cos(z)
    if family == "diamond":                        # Schwarz Diamond
        return np.cos(x) * np.cos(y) * np.cos(z) - np.sin(x) * np.sin(y) * np.sin(z)
    raise ValueError(f"unknown TPMS family: {family}")


_ABSF_CACHE: dict = {}


def _absF_sorted(family: str, n: int = 48) -> np.ndarray:
    """Sorted |F| over one unit cell — the empirical CDF used to map density->wall."""
    if family not in _ABSF_CACHE:
        lin = np.linspace(0, 2 * np.pi, n, endpoint=False)
        gx, gy, gz = np.meshgrid(lin, lin, lin, indexing="ij")
        a = np.abs(_tpms(family, gx, gy, gz)).ravel()
        a.sort()
        _ABSF_CACHE[family] = a
    return _ABSF_CACHE[family]


def _wall_for_density(family, density):
    """Wall threshold s.t. fraction(|F| <= wall) == density.

    The wall<->density map is nonlinear and TPMS-type-specific, so it is
    CALIBRATED numerically (never assumed closed-form): density is exactly the
    density-quantile of |F| over one unit cell. Accepts a scalar or array.
    density=1.0 -> fully solid. See `_calibrate` / `python -m lattice --calibrate`.
    """
    a = _absF_sorted(family)
    return np.interp(np.clip(density, 0.0, 1.0), np.linspace(0, 1, len(a)), a)


# --- per-zone density / family field ---------------------------------------

def _lattice_requested(fill) -> bool:
    """True only if some zone actually asks for a sub-solid TPMS lattice."""
    if fill is None:
        return False
    for z in ZONES:
        zf = getattr(fill, z)
        # family=="solid" forces solid regardless of density; density>=1 is solid too
        if zf.family != "solid" and zf.density < 1.0:
            return True
    return False


def zone_density_field(yn: np.ndarray, fill, centers, warnings=None):
    """Blended per-cell (density, family) as a function of normalized length yn∈[0,1].

    Each zone is seeded at its landmark's normalized-length position (`centers`)
    and blended with a gaussian falloff so density varies smoothly across zone
    boundaries. `yn` is longitudinal (heel->toe), so the field is invariant to the
    left/right X-mirror. Returns (density, family) arrays shaped like `yn`.
    """
    dens_z, fam_z = [], []
    for z in ZONES:
        zf = getattr(fill, z)
        fam = zf.family
        if fam not in FAMILIES:                    # can't happen via validated schema
            if warnings is not None:
                warnings.append(f"apply_lattice: unknown family '{fam}' in {z}; using gyroid")
            fam = "gyroid"
        fam_z.append(fam)
        dens_z.append(1.0 if fam == "solid" else float(zf.density))
    W = np.stack([np.exp(-((yn - c) ** 2) / (2 * ZONE_BLEND_SIGMA ** 2)) for c in centers])
    Wsum = W.sum(axis=0)
    Wsum[Wsum == 0] = 1.0
    density = np.tensordot(np.asarray(dens_z), W, axes=(0, 0)) / Wsum   # smooth blend
    family = np.asarray(fam_z)[np.argmax(W, axis=0)]                    # nearest zone
    return density, family


# --- manifold3d boolean helpers --------------------------------------------

def _to_manifold(m: trimesh.Trimesh):
    return m3d.Manifold(m3d.Mesh(vert_properties=np.asarray(m.vertices, np.float32),
                                 tri_verts=np.asarray(m.faces, np.uint32)))


def _manifold_to_trimesh(man) -> trimesh.Trimesh:
    mm = man.to_mesh()
    # process=False: manifold3d already emits clean, shared-vertex, manifold topology;
    # trimesh's vertex-merge (process=True) would re-weld it into non-manifold edges.
    out = trimesh.Trimesh(vertices=np.asarray(mm.vert_properties[:, :3], float),
                          faces=np.asarray(mm.tri_verts), process=False)
    out.fix_normals()
    if out.volume < 0:                              # manifold3d winding is inward-first
        out.invert()
    return out


# --- entry point ------------------------------------------------------------

def apply_lattice(solid: trimesh.Trimesh, hf, top, mask, rx: FootRx,
                  warnings=None) -> trimesh.Trimesh:
    """Replace `solid`'s interior with a per-zone TPMS lattice; no-op if unrequested.

    `solid, hf, top, mask` are elements 1,3,4,5 of the generate_orthotic tuple.
    Returns `solid` unchanged when `rx.fill` is absent, every zone is solid, or
    density is 1.0 everywhere (a true no-op — byte-identical solid path). Otherwise
    returns a watertight lattice-filled solid whose outer surface/floor come from
    `solid` and whose boundary keeps >= MIN_THICKNESS_MM of solid before the
    lattice begins.
    """
    fill = getattr(rx, "fill", None)
    if not _lattice_requested(fill):
        return solid                                    # true no-op (solid default)

    try:
        # 1) voxelize the finished solid (its own frame; mirror-agnostic)
        p = LATTICE_PITCH_MM
        vg = solid.voxelized(pitch=p).fill()
        inside = np.asarray(vg.matrix, dtype=bool)
        if inside.sum() == 0:
            return solid
        origin = np.asarray(vg.transform)[:3, 3]        # world coord of index (0,0,0)
        ix, iy, iz = np.indices(inside.shape)
        X = origin[0] + ix * p
        Y = origin[1] + iy * p
        Z = origin[2] + iz * p

        # 2) solid skin band: everything within MIN_THICKNESS of a boundary stays solid
        dist_mm = ndimage.distance_transform_edt(inside) * p
        skin = inside & (dist_mm < MIN_THICKNESS_MM)

        # 3) per-voxel density + family from the prescription's zones (function of
        #    normalized length -> invariant to the left/right mirror)
        centers = [float(hf.yn()[hf.landmark(ZONE_TO_LANDMARK[z])[0]]) for z in ZONES]
        y_heel_w = hf.y0 + hf.y_heel * hf.cell
        length = max((hf.y_toe - hf.y_heel) * hf.cell, 1e-6)
        yn = np.clip((Y - y_heel_w) / length, 0.0, 1.0)
        density, family = zone_density_field(yn, fill, centers, warnings)

        # 4) TPMS strut per voxel: |F| <= wall(density), wall calibrated per family
        k = 2 * np.pi / UNIT_CELL_MM
        strut = np.zeros(inside.shape, dtype=bool)
        for fam in set(family.ravel()) - {"solid"}:
            fm = (family == fam) & inside
            if not fm.any():
                continue
            F = _tpms(fam, k * X[fm], k * Y[fm], k * Z[fm])
            wall = _wall_for_density(fam, density[fm])
            strut[fm] = np.abs(F) <= wall
        solid_zone = (family == "solid")                # zones asked to stay solid

        occ = inside & (skin | strut | solid_zone)

        # 5) keep only the body containing the skin shell (drop loose interior specks)
        lbl, _ = ndimage.label(occ)
        keep = np.unique(lbl[skin])
        occ = np.isin(lbl, keep[keep > 0])

        # 6) marching cubes on the padded occupancy -> watertight strut mesh in world frame
        occf = np.pad(occ.astype(np.float32), 1)
        verts, faces, _, _ = measure.marching_cubes(occf, level=0.5, spacing=(p, p, p))
        verts += origin - p                             # undo pad + index-0 origin
        lattice_mesh = trimesh.Trimesh(vertices=verts, faces=faces, process=True)

        # 7) intersect with the original solid: outer skin/floor come from `solid`
        #    exactly, result is watertight and <= solid volume (manifold3d backend).
        #    NB: manifold3d overloads `^` as the intersection (boolean AND) operator.
        result = _manifold_to_trimesh(_to_manifold(solid) ^ _to_manifold(lattice_mesh))
    except BaseException as e:
        if warnings is not None:
            warnings.append(f"apply_lattice: lattice generation failed ({e}); exported solid")
        return solid

    # 8) guard: never ship a non-watertight or degenerate part — fall back to solid
    if (not result.is_watertight) or result.volume < 1000:
        if warnings is not None:
            warnings.append("apply_lattice: lattice result failed validation; exported solid")
        return solid
    return result


# --- numeric calibration proof (python -m lattice --calibrate) -------------

def _calibrate(family: str = "gyroid", n: int = 96, tol: float = 0.05) -> bool:
    """Measure occupied-voxel fraction vs target density on an independent grid,
    proving the density<->wall map was measured (not assumed). Prints a table."""
    lin = np.linspace(0, 2 * np.pi, n, endpoint=False)
    gx, gy, gz = np.meshgrid(lin, lin, lin, indexing="ij")
    absF = np.abs(_tpms(family, gx, gy, gz))
    print(f"TPMS density calibration ({family}, {n}^3 independent samples):")
    print(f"{'target':>8} {'wall':>8} {'measured':>10} {'abs_err':>8}")
    worst = 0.0
    for d in [0.2, 0.35, 0.5, 0.65, 0.8]:
        wall = float(_wall_for_density(family, d))
        meas = float((absF <= wall).mean())
        err = abs(meas - d)
        worst = max(worst, err)
        print(f"{d:8.2f} {wall:8.3f} {meas:10.3f} {err:8.3f}")
    ok = worst <= tol
    print(f"max abs error {worst:.3f} (tolerance {tol}) -> {'PASS' if ok else 'FAIL'}")
    return ok


if __name__ == "__main__":
    import sys
    if "--calibrate" in sys.argv:
        for fam in ("gyroid", "diamond", "primitive"):
            _calibrate(fam)
            print()
    else:
        print("usage: python -m lattice --calibrate")
