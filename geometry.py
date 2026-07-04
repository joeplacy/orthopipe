"""
OrthoPipe geometry engine (prototype).

Pipeline per foot:
  load .obj -> clean -> align to canonical frame -> plantar heightfield
  -> orthotic shell (heightfield solid) -> apply Rx mods -> validate -> export STL

Canonical frame (RIGHT-foot internal convention; left feet are mirrored in and back out):
  +Y heel(0) -> toes(L)   |   +X medial   |   +Z up   |   plantar min z = 0   | units mm

Design notes:
- All mods are heightfield (Z(x,y)) operations => no mesh booleans => robust.
- Deterministic: same Rx + same scan => identical STL.
"""
from __future__ import annotations
import numpy as np
import trimesh
from scipy import ndimage
from scipy.interpolate import griddata

from schema import FootRx, Shell

CELL_MM = 2.0          # heightfield resolution
MIN_THICKNESS_MM = 2.0 # never thin the shell below this


# ---------------------------------------------------------------- cleanup ----

def clean_scan(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Keep largest connected component, drop degenerate faces, light smooth.
    (In production, run the pymeshlab close-holes/smooth recipe here first.)"""
    parts = mesh.split(only_watertight=False)
    if len(parts) > 1:
        mesh = max(parts, key=lambda p: p.area)
    mesh.update_faces(mesh.nondegenerate_faces())
    mesh.remove_unreferenced_vertices()
    try:
        trimesh.smoothing.filter_taubin(mesh, lamb=0.5, nu=-0.53, iterations=6)
    except BaseException:
        pass
    return mesh


# -------------------------------------------------------------- alignment ----

def _width_profile(v: np.ndarray, nbins=20):
    ymin, ymax = v[:, 1].min(), v[:, 1].max()
    edges = np.linspace(ymin, ymax, nbins + 1)
    widths = np.zeros(nbins)
    for k in range(nbins):
        sel = (v[:, 1] >= edges[k]) & (v[:, 1] < edges[k + 1])
        if sel.sum() > 5:
            widths[k] = v[sel, 0].max() - v[sel, 0].min()
    return widths


def align_canonical(mesh: trimesh.Trimesh, side: str) -> tuple[trimesh.Trimesh, list[str]]:
    """PCA + physical heuristics -> canonical right-foot frame. Returns (mesh, warnings)."""
    warnings = []
    m = mesh.copy()
    if side == "left":  # mirror into right-foot convention; mirrored back on export
        m.apply_transform(trimesh.transformations.reflection_matrix([0, 0, 0], [1, 0, 0]))
        m.invert()

    # 1) PCA: longest axis -> Y, next -> X, smallest -> Z
    v = m.vertices - m.vertices.mean(axis=0)
    _, _, Vt = np.linalg.svd(v, full_matrices=False)
    R = np.eye(4)
    R[:3, :3] = np.array([Vt[1], Vt[0], Vt[2]])  # rows: new X, Y, Z in old coords
    if np.linalg.det(R[:3, :3]) < 0:
        R[2, :3] *= -1
    m.apply_translation(-mesh.vertices.mean(axis=0))
    m.apply_transform(R)

    # 2) Z sign: plantar sheet is flat with arch bumping to +Z => positive skew
    z = m.vertices[:, 2]
    if (z.mean() - np.median(z)) < 0:
        m.apply_transform(trimesh.transformations.rotation_matrix(np.pi, [0, 1, 0]))

    # 3) Heel vs toe: forefoot (met heads) is the widest region => widest bin in upper half
    w = _width_profile(m.vertices)
    if np.argmax(w) < len(w) / 2:
        m.apply_transform(trimesh.transformations.rotation_matrix(np.pi, [0, 0, 1]))

    # 4) Medial side check: arch (raised z) sits medial (+X) in midfoot band.
    v = m.vertices
    L = v[:, 1].max() - v[:, 1].min()
    band = (v[:, 1] > v[:, 1].min() + 0.35 * L) & (v[:, 1] < v[:, 1].min() + 0.60 * L)
    med = v[band & (v[:, 0] > 0)][:, 2]
    lat = v[band & (v[:, 0] < 0)][:, 2]
    if len(med) > 10 and len(lat) > 10 and med.mean() < lat.mean():
        # arch on -X: rotate 180 about Y (flips X and Z, preserves chirality), redo z-level below
        m.apply_transform(trimesh.transformations.rotation_matrix(np.pi, [0, 1, 0]))
        z = m.vertices[:, 2]
        if (z.mean() - np.median(z)) < 0:
            warnings.append("medial/z heuristics disagreed; flag for reviewer")

    # 5) Level: least-squares plane on the lowest 25% of vertices -> Z=0
    v = m.vertices
    low = v[v[:, 2] < np.quantile(v[:, 2], 0.25)]
    A = np.c_[low[:, 0], low[:, 1], np.ones(len(low))]
    coef, *_ = np.linalg.lstsq(A, low[:, 2], rcond=None)
    n = np.array([-coef[0], -coef[1], 1.0]); n /= np.linalg.norm(n)
    axis = np.cross(n, [0, 0, 1.0])
    if np.linalg.norm(axis) > 1e-8:
        ang = float(np.arccos(np.clip(n @ [0, 0, 1.0], -1, 1)))
        m.apply_transform(trimesh.transformations.rotation_matrix(ang, axis))

    # 6) Origin: heel at y=0, plantar floor at z=0, footprint centered in x
    v = m.vertices
    m.apply_translation([-np.median(v[:, 0]), -v[:, 1].min(), -np.quantile(v[:, 2], 0.02)])
    return m, warnings


# ------------------------------------------------------------ heightfield ----

class Heightfield:
    """Plantar surface as Z(x,y) on a regular grid + validity mask."""

    def __init__(self, mesh: trimesh.Trimesh, cell=CELL_MM):
        v = mesh.vertices
        self.cell = cell
        self.x0, self.y0 = v[:, 0].min(), v[:, 1].min()
        nx = int(np.ceil((v[:, 0].max() - self.x0) / cell)) + 1
        ny = int(np.ceil((v[:, 1].max() - self.y0) / cell)) + 1
        ix = np.clip(((v[:, 0] - self.x0) / cell).astype(int), 0, nx - 1)
        iy = np.clip(((v[:, 1] - self.y0) / cell).astype(int), 0, ny - 1)
        Z = np.full((ny, nx), np.inf)
        np.minimum.at(Z, (iy, ix), v[:, 2])          # lowest surface = plantar
        mask = np.isfinite(Z)
        mask = ndimage.binary_closing(mask, iterations=2)
        mask = ndimage.binary_fill_holes(mask)
        mask = ndimage.binary_erosion(mask, iterations=2)  # shave noisy rolled-up rim
        # interpolate missing cells inside the footprint
        yy, xx = np.mgrid[0:ny, 0:nx]
        known = np.isfinite(Z)
        Z = griddata((yy[known], xx[known]), Z[known], (yy, xx), method="nearest")
        Z = ndimage.gaussian_filter(Z, sigma=1.2)
        Z = np.clip(Z - np.quantile(Z[mask], 0.02), 0, 30.0)  # re-floor, clamp rollup
        self.Z, self.mask = Z, mask
        self.ny, self.nx = ny, nx
        ys = np.where(mask.any(axis=1))[0]
        self.y_heel, self.y_toe = ys.min(), ys.max()
        self.L = (self.y_toe - self.y_heel) * cell    # foot length, mm

    # grid coords helpers -------------------------------------------------
    def yn(self):
        """normalized length coordinate per row: 0 at heel, 1 at toe"""
        j = np.arange(self.ny)
        return np.clip((j - self.y_heel) / max(self.y_toe - self.y_heel, 1), 0, 1)

    def landmark(self, name: str):
        """(row, col) of anatomical landmark via heuristics on the canonical footprint."""
        yn = self.yn()
        def row_at(f): return int(self.y_heel + f * (self.y_toe - self.y_heel))
        if name == "heel_center":
            j = row_at(0.10); cols = np.where(self.mask[j])[0]
            return j, int(cols.mean())
        if name == "base_5th_met":                    # lateral (-X) extreme ~63% of length
            j = row_at(0.63); cols = np.where(self.mask[j])[0]
            return j, int(cols.min() + 1)
        if name == "met_heads":
            j = row_at(0.72); cols = np.where(self.mask[j])[0]
            return j, int(cols.mean())
        if name == "arch_apex":                       # medial midfoot
            j = row_at(0.45); cols = np.where(self.mask[j])[0]
            return j, int(cols.max() - max(2, len(cols) // 5))
        if name == "hallux":
            j = row_at(0.90); cols = np.where(self.mask[j])[0]
            return j, int(cols.max() - len(cols) // 4)
        raise ValueError(name)


# ------------------------------------------------------------ shell + mods ----

def _smoothstep(t):
    t = np.clip(t, 0, 1)
    return t * t * (3 - 2 * t)


def build_shell(hf: Heightfield, shell: Shell):
    """Returns (top, mask): top surface z over bottom plane z=0, trimmed."""
    mask = hf.mask.copy()
    yn = hf.yn()[:, None] * np.ones((1, hf.nx))
    trim_at = {"full": 1.01, "3quarter": 0.70, "sulcus": 0.80}[shell.trim]
    mask &= yn <= trim_at
    top = hf.Z + shell.thickness_mm                       # conforming top, flat bottom @ z=0

    if shell.heel_cup_depth_mm > 0:                       # raise rim around the heel
        dist = ndimage.distance_transform_edt(mask) * hf.cell
        rim = _smoothstep(1 - dist / 14.0)                # within ~14mm of the edge
        heel_zone = _smoothstep((0.35 - yn) / 0.15)       # rear third, feathered
        top += shell.heel_cup_depth_mm * rim * heel_zone
    return top, mask


def apply_mods(top: np.ndarray, mask: np.ndarray, hf: Heightfield, rx: FootRx):
    """Each mod is a pure heightfield operation. Order is fixed here, never by the LLM."""
    yn = hf.yn()[:, None] * np.ones((1, hf.nx))
    xs = (np.arange(hf.nx) * hf.cell + hf.x0)[None, :] * np.ones((hf.ny, 1))
    applied = []
    order = {"heel_lift": 0, "medial_wedge": 1, "lateral_wedge": 1, "met_pad": 2, "relief": 3}
    for mod in sorted(rx.mods, key=lambda m: order[m.type]):
        if mod.type == "heel_lift":
            ramp = _smoothstep((0.55 - yn) / 0.45)         # full at heel, 0 by 55% length
            top += mod.height_mm * ramp
        elif mod.type in ("medial_wedge", "lateral_wedge"):
            slope = np.tan(np.radians(mod.degrees))
            x_lat = xs[mask].min() if mod.type == "medial_wedge" else xs[mask].max()
            dist = np.abs(xs - x_lat)                      # thickest on named side
            taper = _smoothstep((0.75 - yn) / 0.20)        # fade out at forefoot
            top += slope * dist * taper
        elif mod.type == "met_pad":
            j, i = hf.landmark("met_heads")
            j -= int(12 / hf.cell)                         # dome sits proximal to met heads
            r = 16 / hf.cell
            jj, ii = np.mgrid[0:hf.ny, 0:hf.nx]
            top += mod.height_mm * np.exp(-(((jj - j) ** 2 + (ii - i) ** 2) / (2 * r * r)))
        elif mod.type == "relief":
            j, i = hf.landmark(mod.landmark)
            r = mod.radius_mm / hf.cell
            jj, ii = np.mgrid[0:hf.ny, 0:hf.nx]
            dip = mod.depth_mm * np.exp(-(((jj - j) ** 2 + (ii - i) ** 2) / (2 * r * r)))
            top = np.maximum(top - dip, MIN_THICKNESS_MM)  # never below min thickness
        applied.append(mod.type)
    top = np.maximum(top, MIN_THICKNESS_MM)
    return top, applied


# ----------------------------------------------------- heightfield -> solid ----

def solid_from_heightfield(top: np.ndarray, mask: np.ndarray, hf: Heightfield) -> trimesh.Trimesh:
    """Watertight solid: triangulated top + flat bottom + boundary walls. No booleans."""
    ny, nx = mask.shape
    node_id = -np.ones((ny, nx), dtype=int)
    verts = []
    for j in range(ny):
        for i in range(nx):
            if mask[j, i]:
                node_id[j, i] = len(verts)
                verts.append([hf.x0 + i * hf.cell, hf.y0 + j * hf.cell, top[j, i]])
    n_top = len(verts)
    verts += [[v[0], v[1], 0.0] for v in verts]            # bottom twins

    faces = []
    for j in range(ny - 1):
        for i in range(nx - 1):
            a, b = node_id[j, i], node_id[j, i + 1]
            c, d = node_id[j + 1, i], node_id[j + 1, i + 1]
            if min(a, b, c, d) >= 0:
                faces += [[a, b, d], [a, d, c]]            # top (CCW from +Z)
                A, B, C, D = a + n_top, b + n_top, c + n_top, d + n_top
                faces += [[A, D, B], [A, C, D]]            # bottom (flipped)

    # boundary walls: directed edges used exactly once by the top surface
    from collections import Counter
    edge_count = Counter()
    for f in faces[:len(faces)]:
        if max(f) < n_top:                                 # top faces only
            for e in ((f[0], f[1]), (f[1], f[2]), (f[2], f[0])):
                edge_count[tuple(sorted(e))] += 1
    for f in [f for f in faces if max(f) < n_top]:
        for a, b in ((f[0], f[1]), (f[1], f[2]), (f[2], f[0])):
            if edge_count[tuple(sorted((a, b)))] == 1:
                faces += [[b, a, a + n_top], [b, a + n_top, b + n_top]]

    m = trimesh.Trimesh(vertices=np.array(verts), faces=np.array(faces), process=True)
    m.fix_normals()
    if m.volume < 0:
        m.invert()
    return m


# ------------------------------------------------------------- validation ----

def validate(mesh: trimesh.Trimesh, hf: Heightfield) -> dict:
    ext = mesh.extents
    checks = {
        "watertight": bool(mesh.is_watertight),
        "positive_volume": bool(mesh.volume > 1000),
        "length_plausible": bool(120 <= ext[1] <= 360),
        "width_plausible": bool(40 <= ext[0] <= 140),
        "height_plausible": bool(ext[2] <= 60),
    }
    return {"pass": all(checks.values()), "checks": checks,
            "volume_cm3": round(float(mesh.volume) / 1000, 1),
            "extents_mm": [round(float(e), 1) for e in ext],
            "est_print_mass_g_tpu": round(float(mesh.volume) / 1000 * 1.22, 0)}


# -------------------------------------------------------------- top level ----

def generate_orthotic(scan_path: str, side: str, rx: FootRx, shell: Shell, out_stl: str):
    scan = trimesh.load(scan_path, force="mesh")
    scan = clean_scan(scan)
    aligned, warnings = align_canonical(scan, side)
    hf = Heightfield(aligned)
    top, mask = build_shell(hf, shell)
    top, applied = apply_mods(top, mask, hf, rx)
    solid = solid_from_heightfield(top, mask, hf)
    if side == "left":                                     # mirror back to true left
        solid.apply_transform(trimesh.transformations.reflection_matrix([0, 0, 0], [1, 0, 0]))
    if solid.volume < 0:                                   # normalize winding outward
        solid.invert()
    solid.export(out_stl)
    report = validate(solid, hf)
    report.update({"side": side, "mods_applied": applied, "warnings": warnings,
                   "foot_length_mm": round(hf.L, 1), "output": out_stl})
    return solid, aligned, hf, top, mask, report
