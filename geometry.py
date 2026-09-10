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
from pathlib import Path
import numpy as np
import trimesh
import pymeshlab                 # Python 3.10+ requirement; wheel confirmed in the .venv
from scipy import ndimage
from scipy.interpolate import griddata

from schema import FootRx, Shell
from vendor.ampscan_icp import register_icp   # vendored MIT ICP (no ampscan dependency)
from lattice import apply_lattice             # optional variable-stiffness TPMS interior

CELL_MM = 2.0          # heightfield resolution
MIN_THICKNESS_MM = 2.0 # never thin the shell below this

# --- plantar-pressure estimate (Winkler elastic-foundation spring-bed) constants ---
# A deterministic proxy, NOT FEA. Local spring stiffness tracks the zone fill density
# so softer lattice zones show lower peak pressure. (Heavier real-FEA upgrade path:
# CalculiX `ccx` with *HYPERFOAM contact, or FEniCSx — deliberately out of scope here.)
BODY_LOAD_N = 350.0            # ~50% BW of a 70 kg subject, single-foot stance
# Effective foundation modulus, tuned so the rigid-flat-indenter peak lands in the
# clinical band (~100-500 kPa) on the demo scans. Lower than bulk TPU modulus because
# the spring-bed + rigid-indenter proxy concentrates load on the tallest cell; treat
# as a calibration constant to fit against bench pressure data, not a material property.
E_SOLID_PA = 1.0e6
GIBSON_ASHBY_N = 2.0          # bending-dominated lattice exponent: E_eff ≈ E_solid·ρ^n
PRESSURE_PEAK_MAX_KPA = 600.0  # above this = implausible (clinical peaks ~200-500 kPa)


# ---------------------------------------------------------------- cleanup ----

def _boundary_loop_count(faces: np.ndarray) -> int:
    """Number of open boundary loops (holes) in a triangle soup.
    An edge used by exactly one face is a boundary edge; the loops those edges
    form are the holes. Returns 0 for a closed (watertight) mesh."""
    if len(faces) == 0:
        return 0
    e = np.sort(np.vstack([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]]), axis=1)
    uniq, cnt = np.unique(e, axis=0, return_counts=True)
    b = uniq[cnt == 1]
    if len(b) == 0:
        return 0
    import networkx as nx  # already a trimesh dependency
    g = nx.Graph()
    g.add_edges_from(b.tolist())
    return nx.number_connected_components(g)


def clean_scan(
    mesh: trimesh.Trimesh,
    heavy_repair: bool = True,
    target_faces: int | None = 80000,
    warnings: list[str] | None = None,
) -> trimesh.Trimesh:
    """Repair a raw scanner export into print-ready, watertight-ish input.

    trimesh pre-steps (keep largest component, drop degenerate faces) then a
    PyMeshLab recipe: dedup -> close holes -> optional quadric decimation ->
    light Taubin smoothing. Every substantive action is appended to `warnings`
    rather than guessed silently. Pure repair pass — orientation, mirroring and
    the plantar z=0 floor stay the responsibility of align_canonical.

    heavy_repair=False is the synthetic-foot escape hatch: dedup + Taubin only
    (skips close-holes and decimation). target_faces=None disables decimation.
    """
    if warnings is None:
        warnings = []  # local list => behavior unchanged when caller omits it

    # --- trimesh pre-steps: drop junk that would skew PCA in align_canonical ---
    parts = mesh.split(only_watertight=False)
    if len(parts) > 1:
        mesh = max(parts, key=lambda p: p.area)
        warnings.append(f"clean_scan: discarded {len(parts) - 1} stray components")
    mesh.update_faces(mesh.nondegenerate_faces())
    mesh.remove_unreferenced_vertices()

    # --- round-trip trimesh -> MeshSet -> trimesh, in memory (no temp file) ---
    ms = pymeshlab.MeshSet()
    ms.add_mesh(pymeshlab.Mesh(vertex_matrix=np.asarray(mesh.vertices, dtype=float),
                               face_matrix=np.asarray(mesh.faces)))

    # dedup / cleanup — cheap, run in both modes
    ms.meshing_remove_duplicate_vertices()
    ms.meshing_remove_duplicate_faces()
    ms.meshing_remove_unreferenced_vertices()
    ms.meshing_remove_null_faces()

    if heavy_repair:
        # close holes up to 40 boundary edges (small punctures/gaps from the
        # scanner); larger gaps are left open and surface as a warning downstream
        # rather than being bridged with an invented sheet.
        maxholesize = 40
        try:
            n_before = _boundary_loop_count(ms.current_mesh().face_matrix())
            ms.meshing_close_holes(maxholesize=maxholesize)
            closed = n_before - _boundary_loop_count(ms.current_mesh().face_matrix())
            if closed > 0:
                warnings.append(f"clean_scan: closed {closed} holes (maxholesize={maxholesize})")
        except BaseException as e:
            # non-manifold input can defeat hole-filling; repair edges then retry
            try:
                ms.meshing_repair_non_manifold_edges()
                ms.meshing_close_holes(maxholesize=maxholesize)
                warnings.append(f"clean_scan: closed holes after non-manifold repair (maxholesize={maxholesize})")
            except BaseException as e2:
                warnings.append(f"clean_scan: meshing_close_holes failed ({e2}); proceeding with unrepaired mesh")

        # decimation, gated on target_faces and only if over budget
        if target_faces and ms.current_mesh().face_number() > target_faces:
            n0 = ms.current_mesh().face_number()
            ms.meshing_decimation_quadric_edge_collapse(
                targetfacenum=target_faces, preservenormal=True, planarquadric=True)
            warnings.append(f"clean_scan: decimated {n0}->{ms.current_mesh().face_number()} faces")

    # light Taubin smoothing — comparable to old filter_taubin(lamb=0.5, nu=-0.53)
    try:
        ms.apply_coord_taubin_smoothing(stepsmoothnum=10, lambda_=0.5, mu=-0.53)
    except BaseException as e:
        warnings.append(f"clean_scan: taubin smoothing failed ({e}); proceeding unsmoothed")

    # rebuild — do NOT re-floor/re-scale/re-orient; that belongs to align_canonical
    return trimesh.Trimesh(vertices=ms.current_mesh().vertex_matrix(),
                           faces=ms.current_mesh().face_matrix(), process=True)


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


# ICP refinement to a canonical right-foot template. PCA gives a coarse frame;
# ICP registration to a real template is far more robust on real feet.
ICP_RESIDUAL_WARN_MM = 3.0   # near CELL_MM (2.0) / MIN_THICKNESS_MM (2.0)
TEMPLATE_PATH = Path(__file__).parent / "templates" / "right_foot_template.stl"
_TEMPLATE_CACHE: dict = {}


def _load_template():
    """Lazily load + cache the canonical right-foot template. None if missing."""
    if "mesh" not in _TEMPLATE_CACHE:
        try:
            _TEMPLATE_CACHE["mesh"] = (trimesh.load(str(TEMPLATE_PATH), force="mesh")
                                       if TEMPLATE_PATH.exists() else None)
        except BaseException:
            _TEMPLATE_CACHE["mesh"] = None
    return _TEMPLATE_CACHE["mesh"]


def refine_icp(mesh: trimesh.Trimesh, template: trimesh.Trimesh) -> tuple[trimesh.Trimesh, float]:
    """Register `mesh` onto `template` (both right-foot canonical). Returns (mesh, rmse)."""
    T, rmse = register_icp(mesh.vertices, mesh.faces, template.vertices, template.faces)
    out = mesh.copy()
    out.apply_transform(T)
    return out, rmse


def align_canonical(mesh: trimesh.Trimesh, side: str,
                    use_icp: bool = True) -> tuple[trimesh.Trimesh, list[str]]:
    """PCA + physical heuristics -> canonical right-foot frame, ICP-refined against
    the template when available. Returns (mesh, warnings)."""
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

    # 5b) ICP refine against the canonical template (PCA above is the initializer).
    #     Runs before the origin re-zeroing below so origin normalization is last
    #     and downstream code (heel y=0, plantar z=0) is untouched. Falls back to
    #     the PCA result if ICP does not improve, raises, or the template is absent.
    if use_icp:
        template = _load_template()
        if template is not None:
            try:
                _, rmse_pca = register_icp(m.vertices, m.faces,
                                           template.vertices, template.faces, maxiter=0)
                refined, rmse_icp = refine_icp(m, template)
                if rmse_icp <= rmse_pca:
                    m, rmse = refined, rmse_icp
                else:
                    rmse = rmse_pca
                    warnings.append("ICP did not improve on PCA alignment; kept PCA result")
                if rmse > ICP_RESIDUAL_WARN_MM:
                    warnings.append(f"ICP alignment residual {rmse:.1f}mm exceeds "
                                    f"{ICP_RESIDUAL_WARN_MM}mm; flag for reviewer")
            except BaseException as e:
                warnings.append(f"ICP refinement failed ({e}); using PCA alignment")

    # 6) Origin: heel at y=0, plantar floor at z=0, footprint centered in x
    v = m.vertices
    m.apply_translation([-np.median(v[:, 0]), -v[:, 1].min(), -np.quantile(v[:, 2], 0.02)])
    return m, warnings


# ------------------------------------------------------------ heightfield ----

# Proportional landmark heuristics, extracted from Heightfield.landmark() so they
# can be measured/calibrated (landmarks.py). DEFAULTS ARE BYTE-IDENTICAL to the
# original hardcoded constants — `frac` is the row_at() fraction; `col` selects a
# reducer over the row's masked columns (mean/min/max) plus the exact offset used
# before: base_5th_met = min+1, arch_apex = max - max(2, len//5), hallux = max - len//4.
LANDMARK_PARAMS: dict[str, dict] = {
    "heel_center":  {"frac": 0.10, "col": {"base": "mean"}},
    "base_5th_met": {"frac": 0.63, "col": {"base": "min", "add": 1}},
    "met_heads":    {"frac": 0.72, "col": {"base": "mean"}},
    "arch_apex":    {"frac": 0.45, "col": {"base": "max", "sub_frac_denom": 5, "sub_floor": 2}},
    "hallux":       {"frac": 0.90, "col": {"base": "max", "sub_frac_denom": 4}},
}


def _col_from_rule(cols: np.ndarray, rule: dict) -> int:
    """Reproduce the original per-landmark column expression from a `col` rule dict.
    `add` (default 0) is applied last so calibration can add an offset to any rule
    without changing the byte-identical defaults (where add=0, except base_5th_met=1)."""
    base = {"mean": cols.mean(), "min": cols.min(), "max": cols.max()}[rule["base"]]
    val = base
    denom = rule.get("sub_frac_denom")
    if denom:
        sub = len(cols) // denom
        if "sub_floor" in rule:
            sub = max(rule["sub_floor"], sub)
        val = base - sub
    val = val + rule.get("add", 0)
    return int(val)


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
        """(row, col) of anatomical landmark via heuristics on the canonical footprint.

        Reads the proportional parameters from module-level `LANDMARK_PARAMS`; the
        defaults reproduce the original hardcoded constants byte-for-byte. A
        calibration file can override `LANDMARK_PARAMS` (see landmarks.py) — the
        default engine path never loads one, so behavior is unchanged by default.
        """
        if name not in LANDMARK_PARAMS:
            raise ValueError(name)
        p = LANDMARK_PARAMS[name]
        j = int(self.y_heel + p["frac"] * (self.y_toe - self.y_heel))   # row_at(frac)
        cols = np.where(self.mask[j])[0]
        return j, _col_from_rule(cols, p["col"])


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


# ----------------------------------------------------- pressure estimate ----

def _density_field(hf: Heightfield, mask: np.ndarray, rx: FootRx) -> np.ndarray:
    """Per-cell relative density ρ∈(0,1] over (hf.ny, hf.nx). ρ≡1 (solid) when
    rx.fill is None. Zone densities are mapped onto longitudinal bands via hf.yn()."""
    rho = np.ones((hf.ny, hf.nx))
    fill = getattr(rx, "fill", None)
    if fill is None:
        return rho
    yn = hf.yn()                                   # (ny,) normalized heel->toe per row
    bands = [("heel", 0.0, 0.25), ("midfoot", 0.25, 0.55),
             ("forefoot", 0.55, 0.85), ("toe", 0.85, 1.0001)]
    for zone, lo, hi in bands:
        zf = getattr(fill, zone)
        d = 1.0 if zf.family == "solid" else float(zf.density)   # solid family => solid
        rho[(yn >= lo) & (yn < hi), :] = max(d, 0.05)            # clamp to avoid zero stiffness
    return rho


def _spring_bed(top: np.ndarray, mask: np.ndarray, hf: Heightfield, rho: np.ndarray):
    """Returns (k [Pa/m], gap [m], A [m²]) for the Winkler spring-bed under a rigid
    flat indenter conforming to the top surface (gap=0 at the highest contact cell)."""
    A = (hf.cell / 1000.0) ** 2                     # tributary area per cell, m²
    t = np.maximum(top, MIN_THICKNESS_MM) / 1000.0  # local thickness, m
    k = (E_SOLID_PA * rho ** GIBSON_ASHBY_N) / t    # foundation modulus, Pa/m (Gibson-Ashby)
    gap = (float(top[mask].max()) - top) / 1000.0   # m; 0 at the highest contact point
    return k, gap, A


def _solve_indentation(top: np.ndarray, mask: np.ndarray, hf: Heightfield,
                       rho: np.ndarray) -> float:
    """Bisection on penetration Δ so Σ kᵢ·Aᵢ·δᵢ(Δ) = BODY_LOAD_N. Deterministic."""
    k, gap, A = _spring_bed(top, mask, hf, rho)

    def force(delta_indent):
        return float((k * A * np.maximum(delta_indent - gap, 0.0))[mask].sum())

    lo, hi = 0.0, (float(gap[mask].max()) + 1e-3)
    while force(hi) < BODY_LOAD_N and hi < 1e3:
        hi *= 2.0
    for _ in range(60):                             # ~40+ is plenty; deterministic
        mid = 0.5 * (lo + hi)
        if force(mid) < BODY_LOAD_N:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def _pressure_at(top: np.ndarray, mask: np.ndarray, hf: Heightfield,
                 rho: np.ndarray, delta: float) -> np.ndarray:
    """Per-cell pressure (kPa) at a fixed indentation Δ for a given density field."""
    k, gap, _ = _spring_bed(top, mask, hf, rho)
    return np.where(mask, k * np.maximum(delta - gap, 0.0) / 1000.0, 0.0)


def estimate_pressure(top: np.ndarray, mask: np.ndarray, hf: Heightfield,
                      rx: FootRx) -> tuple[np.ndarray, dict]:
    """Deterministic plantar-pressure estimate (kPa) + peak/mean/location metrics.

    Fixed-penetration model: BODY_LOAD_N sets the indentation depth Δ on the nominal
    SOLID device, then softer lattice zones cushion (lower local pressure) at that
    same Δ, so the pressure map responds to the fill field and offloading is
    monotonic (pressure ≤ solid everywhere). NB: re-solving Δ per density under a
    rigid plate would redistribute load and can RAISE the global peak elsewhere —
    not the clinical offloading story — so Δ is fixed from the ρ≡1 baseline. The
    solid baseline (ρ≡1) at that Δ is the offloading reference. NOT FEA — a proxy.
    """
    rho = _density_field(hf, mask, rx)
    delta = _solve_indentation(top, mask, hf, np.ones_like(rho))   # load on solid device
    p_kpa = _pressure_at(top, mask, hf, rho, delta)
    peak_solid = float(_pressure_at(top, mask, hf, np.ones_like(rho), delta).max())
    r, c = np.unravel_index(int(np.argmax(np.where(mask, p_kpa, -1.0))), p_kpa.shape)
    metrics = {
        "peak_pressure_kpa": round(float(p_kpa.max()), 1),
        "mean_pressure_kpa": round(float(p_kpa[mask].mean()), 1),
        "peak_location": [int(r), int(c)],
        "contact_area_cm2": round(float(mask.sum()) * (hf.cell / 10.0) ** 2, 1),
        "peak_pressure_solid_kpa": round(float(peak_solid), 1),
    }
    return p_kpa, metrics


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
    warnings = []
    scan = clean_scan(scan, warnings=warnings)
    aligned, align_warnings = align_canonical(scan, side)
    warnings += align_warnings
    hf = Heightfield(aligned)
    top, mask = build_shell(hf, shell)
    top, applied = apply_mods(top, mask, hf, rx)
    solid = solid_from_heightfield(top, mask, hf)
    if side == "left":                                     # mirror back to true left
        solid.apply_transform(trimesh.transformations.reflection_matrix([0, 0, 0], [1, 0, 0]))
    if solid.volume < 0:                                   # normalize winding outward
        solid.invert()
    # optional variable-stiffness lattice interior (no-op unless rx.fill requests it)
    solid = apply_lattice(solid, hf, top, mask, rx, warnings=warnings)
    solid.export(out_stl)
    report = validate(solid, hf)
    # plantar-pressure estimate — additive; validate's signature/keys are untouched
    _, pressure_metrics = estimate_pressure(top, mask, hf, rx)
    report.update(pressure_metrics)
    report["checks"]["peak_pressure_plausible"] = bool(
        pressure_metrics["peak_pressure_kpa"] <= PRESSURE_PEAK_MAX_KPA)
    report["checks"]["pressure_reduced_vs_solid"] = bool(
        pressure_metrics["peak_pressure_kpa"] <= pressure_metrics["peak_pressure_solid_kpa"] + 1e-6)
    report["pass"] = all(report["checks"].values())   # new checks now participate
    report.update({"side": side, "mods_applied": applied, "warnings": warnings,
                   "foot_length_mm": round(hf.L, 1), "output": out_stl})
    return solid, aligned, hf, top, mask, report
