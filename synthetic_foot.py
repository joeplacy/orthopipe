"""
Generate a synthetic 3D foot scan (.obj) that mimics a Comb-style plantar scan:
- foot-shaped plantar surface with a medial arch
- sensor noise
- floating debris components (scan artifacts)
- random rotation + translation (unaligned, like a real export)

Used only for pipeline development until real de-identified scans are available.
"""
import numpy as np
import trimesh


def foot_outline_halfwidth(y_norm: np.ndarray, side: str = "right"):
    """Half-widths (medial, lateral) of a foot outline vs. normalized length.
    y_norm: 0 at heel, 1 at toe tip. Returns (w_medial, w_lateral) in normalized width units.
    """
    # heel ~ narrow/round, midfoot waist, forefoot widest ~75%, rounded toes
    base = (
        0.55 * np.exp(-((y_norm - 0.05) ** 2) / 0.018)   # heel bulge
        + 0.42 * np.exp(-((y_norm - 0.45) ** 2) / 0.09)  # midfoot
        + 0.72 * np.exp(-((y_norm - 0.76) ** 2) / 0.022) # met heads
        + 0.55 * np.exp(-((y_norm - 0.92) ** 2) / 0.012) # toes
    )
    base = np.clip(base, 0.05, None)
    w_lat = base * (1.0 + 0.10 * np.exp(-((y_norm - 0.70) ** 2) / 0.02))  # 5th met flare
    w_med = base * (1.0 + 0.06 * np.exp(-((y_norm - 0.80) ** 2) / 0.02))  # 1st met/hallux
    # midfoot waist is mostly a medial (arch-side) cut-in
    w_med *= 1.0 - 0.28 * np.exp(-((y_norm - 0.45) ** 2) / 0.03)
    return w_med, w_lat


def make_foot_scan(length_mm=260.0, width_mm=98.0, side="right",
                   noise_mm=0.35, seed=7) -> trimesh.Trimesh:
    rng = np.random.default_rng(seed)
    ny, nx = 220, 110
    y = np.linspace(0, 1, ny)
    w_med, w_lat = foot_outline_halfwidth(y, side)
    # medial = +X in this generator's right-foot frame
    verts, faces = [], []
    grid_idx = -np.ones((ny, nx), dtype=int)
    for j, yn in enumerate(y):
        xs = np.linspace(-w_lat[j], w_med[j], nx) * (width_mm / 1.4)
        for i, x in enumerate(xs):
            yn_mm = yn * length_mm
            # plantar surface height: mostly flat, arch dome on medial midfoot,
            # slight heel/toe roll-up at the ends
            arch = 16.0 * np.exp(-((yn - 0.45) ** 2) / 0.035) * \
                   np.exp(-((x / (width_mm / 2) - 0.55) ** 2) / 0.16)
            edge_rollup = 8.0 * np.clip((abs(x) / (xs.max() - xs.min() + 1e-9)) * 2 - 0.82, 0, 1) ** 2
            heel_toe_round = 6.0 * (np.exp(-((yn - 0.0) ** 2) / 0.004) +
                                    np.exp(-((yn - 1.0) ** 2) / 0.004))
            z = arch + edge_rollup + heel_toe_round + rng.normal(0, noise_mm)
            grid_idx[j, i] = len(verts)
            verts.append([x, yn_mm, z])
    for j in range(ny - 1):
        for i in range(nx - 1):
            a, b = grid_idx[j, i], grid_idx[j, i + 1]
            c, d = grid_idx[j + 1, i], grid_idx[j + 1, i + 1]
            faces.append([a, b, d]); faces.append([a, d, c])
    foot = trimesh.Trimesh(vertices=np.array(verts), faces=np.array(faces), process=True)
    if side == "left":
        foot.apply_transform(trimesh.transformations.reflection_matrix([0, 0, 0], [1, 0, 0]))
        foot.invert()

    # scan debris: a few floating blobs
    pieces = [foot]
    for k in range(3):
        blob = trimesh.creation.icosphere(subdivisions=1, radius=rng.uniform(2, 6))
        blob.apply_translation(rng.uniform([-80, -60, 30], [80, 320, 90]))
        pieces.append(blob)
    scan = trimesh.util.concatenate(pieces)

    # random pose, like a raw export
    T = trimesh.transformations.euler_matrix(
        rng.uniform(-0.5, 0.5), rng.uniform(-0.5, 0.5), rng.uniform(-3, 3))
    T[:3, 3] = rng.uniform(-40, 40, 3)
    scan.apply_transform(T)
    return scan


if __name__ == "__main__":
    for side, seed in [("left", 11), ("right", 7)]:
        m = make_foot_scan(side=side, seed=seed)
        m.export(f"/home/claude/orthopipe/sample_scan_{side}.obj")
        print(side, m)
