"""
End-to-end demo using the exact prescription from the process notes:

  "Bilateral Medial Wedges, with a .25 in Heel lift on Right only.
   Lateral base of 5th relief, both feet."

The JSON below is what the Claude Rx-parser stage would emit. Everything after
that is deterministic geometry.
"""
import json
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from schema import Prescription
from synthetic_foot import make_foot_scan
from geometry import generate_orthotic, estimate_pressure

# ---- 0. sample scans (stand-in for the Comb .obj exports) -------------------
for side, seed in [("left", 11), ("right", 7)]:
    make_foot_scan(side=side, seed=seed).export(f"sample_scan_{side}.obj")

# ---- 1. parsed prescription (output of the LLM stage) -----------------------
rx_json = {
    "order_id": "DEMO-0001",
    "left":  {"mods": [{"type": "medial_wedge", "degrees": 4.0},
                        {"type": "relief", "landmark": "base_5th_met",
                         "depth_mm": 2.0, "radius_mm": 12.0}]},
    "right": {"mods": [{"type": "medial_wedge", "degrees": 4.0},
                        {"type": "heel_lift", "height_mm": 6.35},
                        {"type": "relief", "landmark": "base_5th_met",
                         "depth_mm": 2.0, "radius_mm": 12.0}]},
    "shell": {"thickness_mm": 3.5, "trim": "3quarter", "heel_cup_depth_mm": 10.0},
}
rx = Prescription(**rx_json)
print("Parsed Rx OK:\n", json.dumps(rx.model_dump(), indent=2)[:400], "...\n")

# ---- 2. generate both feet ---------------------------------------------------
results = {}
for side in ["left", "right"]:
    foot_rx = getattr(rx, side)
    solid, aligned, hf, top, mask, report = generate_orthotic(
        f"sample_scan_{side}.obj", side, foot_rx, rx.shell,
        f"{rx.order_id}_{side}.stl")
    results[side] = (solid, aligned, hf, top, mask, report)
    print(json.dumps(report, indent=2))

# ---- 3. reviewer previews ----------------------------------------------------
for side, (solid, aligned, hf, top, mask, report) in results.items():
    fig = plt.figure(figsize=(14, 10))
    fig.suptitle(f"Order {rx.order_id} — {side.upper()} — "
                 f"{'PASS' if report['pass'] else 'FAIL'} — mods: {report['mods_applied']}",
                 fontsize=13)

    ax = fig.add_subplot(2, 2, 1, projection="3d")
    v = aligned.vertices[::15]
    ax.scatter(v[:, 0], v[:, 1], v[:, 2], s=1, c=v[:, 2], cmap="viridis")
    ax.set_title("Aligned scan (canonical frame)"); ax.set_box_aspect((1, 2.5, 0.6))

    ax = fig.add_subplot(2, 2, 2)
    Zshow = np.where(mask, top, np.nan)
    im = ax.imshow(Zshow, origin="lower", cmap="turbo",
                   extent=[hf.x0, hf.x0 + hf.nx * hf.cell, hf.y0, hf.y0 + hf.ny * hf.cell])
    for lm in ["base_5th_met", "met_heads", "heel_center", "arch_apex"]:
        j, i = hf.landmark(lm)
        ax.plot(hf.x0 + i * hf.cell, hf.y0 + j * hf.cell, "wx", ms=8)
        ax.annotate(lm, (hf.x0 + i * hf.cell, hf.y0 + j * hf.cell),
                    color="w", fontsize=7, xytext=(4, 4), textcoords="offset points")
    fig.colorbar(im, ax=ax, label="top surface z (mm)")
    ax.set_title("Orthotic top surface + landmarks (right-canonical)")

    ax = fig.add_subplot(2, 2, 3, projection="3d")
    sv, sf = solid.vertices, solid.faces
    ax.plot_trisurf(sv[:, 0], sv[:, 1], sf, sv[:, 2], cmap="cividis",
                    linewidth=0, antialiased=False)
    ax.set_title("Output solid (iso)"); ax.set_box_aspect((1, 2.5, 0.6))

    ax = fig.add_subplot(2, 2, 4)
    mid = np.argmax(mask.sum(axis=0))  # a well-populated column: side profile
    prof_col = np.where(mask[:, mid], top[:, mid], np.nan)
    ys = hf.y0 + np.arange(hf.ny) * hf.cell
    ax.plot(ys, prof_col, label="with Rx mods")
    ax.fill_between(ys, 0, np.nan_to_num(prof_col), alpha=0.3)
    ax.set_xlabel("heel → toe (mm)"); ax.set_ylabel("z (mm)")
    ax.set_title("Sagittal profile (heel lift / cup visible)"); ax.legend(); ax.grid(alpha=0.3)

    fig.tight_layout()
    fig.savefig(f"{rx.order_id}_{side}_preview.png", dpi=110)
    print(f"preview -> {rx.order_id}_{side}_preview.png")

    # plantar-pressure estimate heatmap (Winkler spring-bed) — same Agg preview path
    p_kpa, _ = estimate_pressure(top, mask, hf, getattr(rx, side))
    pfig, pax = plt.subplots(figsize=(5, 8))
    pim = pax.imshow(np.where(mask, p_kpa, np.nan), origin="lower", cmap="inferno",
                     extent=[hf.x0, hf.x0 + hf.nx * hf.cell, hf.y0, hf.y0 + hf.ny * hf.cell])
    pfig.colorbar(pim, ax=pax, label="plantar pressure (kPa)")
    pax.set_title(f"{rx.order_id} — {side.upper()} — peak {report['peak_pressure_kpa']} kPa")
    pfig.tight_layout()
    pfig.savefig(f"{rx.order_id}_{side}_pressure.png", dpi=110)
    plt.close(pfig)
    print(f"pressure -> {rx.order_id}_{side}_pressure.png")
