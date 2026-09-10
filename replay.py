"""
replay.py — historical-order replay + geometric-diff scorecard (batch-2 item 1).

Runs a directory of historical orders (scan + Rx + accepted reference STL) through
`generate_orthotic`, rigid-aligns each produced STL to its reference with the
vendored ICP, and emits a per-order geometric-diff scorecard (surface-deviation
stats, a deviation heatmap PNG, volume delta, clinical cross-sections, landmark
deltas) plus an aggregate summary that flags orders diverging from the batch norm.

Standalone and additive: nothing in the existing pipeline imports this module, and
`generate_orthotic`'s 6-tuple return is consumed as-is (never modified). A
`--synthetic N` mode fabricates order pairs so it runs today against zero real
data, with NO `ANTHROPIC_API_KEY` (Rx is supplied as `rx.json`, never parsed).

CLI:
  python replay.py --synthetic 5 --out /tmp/replay_out      # fabricate + replay
  python replay.py --orders DIR1 DIR2 ... --out OUT          # replay real orders
  python replay.py --selftest                                # identical-mesh sanity
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import trimesh
import matplotlib
matplotlib.use("Agg")                        # headless, same as run_demo.py
import matplotlib.pyplot as plt

from schema import Prescription, FootRx, Shell
from geometry import generate_orthotic, Heightfield
from vendor.ampscan_icp import register_icp

# Obvious-PII denylist for JSON sidecars (case-insensitive). Geometry only survives.
PII_KEYS = {"name", "dob", "mrn", "patient", "ssn", "address", "phone", "email"}
IMPLAUSIBLE_RMSE_MM = 25.0          # ICP residual above this => treat as failed align
LANDMARK_NAMES = ["heel_center", "base_5th_met", "met_heads", "arch_apex", "hallux"]


# --------------------------------------------------------------- de-id ------

def _scrub_pii(obj):
    """Recursively drop obvious-PII keys. Returns (clean_obj, sorted_found_keys)."""
    found: set[str] = set()

    def walk(o):
        if isinstance(o, dict):
            out = {}
            for k, v in o.items():
                if isinstance(k, str) and k.lower() in PII_KEYS:
                    found.add(k)
                    continue
                out[k] = walk(v)
            return out
        if isinstance(o, list):
            return [walk(v) for v in o]
        return o

    return walk(obj), sorted(found)


# ---------------------------------------------------------- ingestion -------

def load_order(order_dir: str | Path) -> dict:
    """Parse an order directory into a de-identified dict.

    Expects: a scan (`scan.obj`/`.stl`/`.ply`), a reference STL (`reference.stl`),
    the driving Rx (`rx.json` — a serialized Prescription or per-foot FootRx —
    and/or `rx_text`), and an optional `order.json` ({order_id, side, shell}).
    `order_id` falls back to the directory name; `side` must be explicit.
    """
    d = Path(order_dir)
    pii_scrubbed: list[str] = []

    meta = {}
    if (d / "order.json").exists():
        meta, s = _scrub_pii(json.loads((d / "order.json").read_text()))
        pii_scrubbed += s
    order_id = str(meta.get("order_id") or d.name)
    side = meta.get("side")
    if side not in ("left", "right"):
        raise ValueError(f"{d}: order requires explicit side in {{'left','right'}}, got {side!r}")
    shell_dict = meta.get("shell", {}) or {}

    scan_path = next((str(d / f"scan{ext}") for ext in (".obj", ".stl", ".ply")
                      if (d / f"scan{ext}").exists()), None)
    if scan_path is None:
        raise ValueError(f"{d}: no scan.obj/.stl/.ply found")
    ref_path = d / "reference.stl"
    if not ref_path.exists():
        raise ValueError(f"{d}: no reference.stl found")

    rx_for_side = {"mods": []}
    rx_text = None
    if (d / "rx.json").exists():
        raw, s = _scrub_pii(json.loads((d / "rx.json").read_text()))
        pii_scrubbed += s
        # accept either a Prescription ({order_id,left,right,shell,...}) or a bare FootRx
        if any(k in raw for k in ("left", "right")):
            rx_for_side = raw.get(side) or {"mods": []}
            shell_dict = shell_dict or raw.get("shell", {}) or {}
        else:
            rx_for_side = raw
    if (d / "rx_text").exists():
        rx_text = (d / "rx_text").read_text().strip()   # parsing is optional (no key needed)

    return {"order_id": order_id, "side": side, "scan_path": scan_path,
            "reference_path": str(ref_path), "rx_for_side": rx_for_side,
            "shell": shell_dict, "rx_text": rx_text,
            "pii_scrubbed": sorted(set(pii_scrubbed))}


# ---------------------------------------------------------- alignment -------

def _align_reference_to_produced(reference: trimesh.Trimesh, produced: trimesh.Trimesh):
    """Rigid-align reference onto the produced part. Returns (aligned_ref, rmse, fallback).

    Primary path is the vendored ICP (mirrors refine_icp's register_icp call). On
    failure or an implausible residual, falls back to a coarse centroid align and
    flags it."""
    try:
        T, rmse = register_icp(reference.vertices, reference.faces,
                               produced.vertices, produced.faces)
        if not np.isfinite(rmse) or rmse > IMPLAUSIBLE_RMSE_MM:
            raise ValueError(f"implausible ICP residual {rmse}")
        aligned = reference.copy()
        aligned.apply_transform(T)
        return aligned, float(rmse), False
    except BaseException:
        aligned = reference.copy()
        aligned.apply_translation(np.asarray(produced.centroid) - np.asarray(reference.centroid))
        _, dist, _ = trimesh.proximity.closest_point(produced, aligned.vertices)
        return aligned, float(np.sqrt((dist ** 2).mean())), True


# ----------------------------------------------------- geometry metrics -----

def _surface_deviation(A: trimesh.Trimesh, B: trimesh.Trimesh):
    """Per-vertex deviation of produced B against reference A. Returns (abs_d, stats)."""
    signed = True
    try:
        d = trimesh.proximity.signed_distance(A, B.vertices)   # mm, signed
        if d is None or len(d) != len(B.vertices):
            raise ValueError("signed_distance shape")
    except BaseException:
        _, d, _ = trimesh.proximity.closest_point(A, B.vertices)  # unsigned fallback
        signed = False
    ad = np.abs(np.asarray(d, dtype=float))
    stats = {"mean_dev": round(float(ad.mean()), 4),
             "p95": round(float(np.percentile(ad, 95)), 4),
             "max_dev": round(float(ad.max()), 4),
             "signed": signed}
    return ad, stats


def _hausdorff(A: trimesh.Trimesh, B: trimesh.Trimesh) -> float:
    """Symmetric Hausdorff = max of the two one-sided vertex→surface maxima (mm)."""
    def one_sided(src, dst):
        _, dist, _ = trimesh.proximity.closest_point(dst, src.vertices)
        return float(dist.max())
    return round(max(one_sided(A, B), one_sided(B, A)), 4)


def _volume_delta(A: trimesh.Trimesh, B: trimesh.Trimesh):
    """(produced - reference) cm^3; sign trustworthy only if both watertight."""
    wt = bool(A.is_watertight and B.is_watertight)
    return round((float(B.volume) - float(A.volume)) / 1000.0, 2), wt


def _section_metrics(mesh: trimesh.Trimesh, hf: Heightfield) -> dict:
    """Clinical cross-section metrics on one mesh, planes placed by hf landmark rows."""
    def world_y(row):
        return hf.y0 + row * hf.cell

    def section_at(y):
        try:
            return mesh.section(plane_origin=[0.0, float(y), 0.0], plane_normal=[0, 1, 0])
        except BaseException:
            return None

    def vspan(sec):                      # vertical (z) span of a transverse section, mm
        if sec is None or len(sec.vertices) == 0:
            return None
        return round(float(np.ptp(sec.vertices[:, 2])), 3)

    def posting_deg(sec):                # tilt of the bottom (posting) face, degrees
        if sec is None or len(sec.vertices) < 4:
            return None
        v = np.asarray(sec.vertices, float)
        zmid = np.median(v[:, 2])
        bottom = v[v[:, 2] <= zmid]      # lower boundary of the section
        if len(bottom) < 2 or np.ptp(bottom[:, 0]) < 1e-6:
            return None
        slope = np.polyfit(bottom[:, 0], bottom[:, 2], 1)[0]
        return round(float(np.degrees(np.arctan(slope))), 3)

    arch_row = hf.landmark("arch_apex")[0]
    heel_row = hf.landmark("heel_center")[0]
    fore_row = hf.landmark("met_heads")[0]
    return {
        "arch_height_mm": vspan(section_at(world_y(arch_row))),
        "heel_cup_depth_mm": vspan(section_at(world_y(heel_row))),
        "rearfoot_posting_deg": posting_deg(section_at(world_y(heel_row))),
        "forefoot_posting_deg": posting_deg(section_at(world_y(fore_row))),
    }


def _cross_sections(A: trimesh.Trimesh, B: trimesh.Trimesh, hf: Heightfield) -> dict:
    """Reference/produced cross-section metrics + deltas (produced - reference)."""
    a, b = _section_metrics(A, hf), _section_metrics(B, hf)
    out = {}
    for key in a:
        av, bv = a[key], b[key]
        delta = round(bv - av, 3) if (av is not None and bv is not None) else None
        out[key] = {"reference": av, "produced": bv, "delta": delta}
    return out


def _landmark_deltas(hf: Heightfield, top: np.ndarray, reference: trimesh.Trimesh) -> dict:
    """3D distance from each produced landmark point to the nearest reference surface point."""
    out = {}
    for name in LANDMARK_NAMES:
        try:
            row, col = hf.landmark(name)
            pt = np.array([[hf.x0 + col * hf.cell, hf.y0 + row * hf.cell, float(top[row, col])]])
            _, dist, _ = trimesh.proximity.closest_point(reference, pt)
            out[name] = round(float(dist[0]), 4)
        except BaseException:
            out[name] = None
    return out


def _heatmap_png(hf: Heightfield, produced: trimesh.Trimesh, abs_d: np.ndarray,
                 png_path: str, order_id: str, side: str):
    """Rasterize |deviation| at produced vertices onto the hf grid and render (Agg)."""
    grid_sum = np.zeros((hf.ny, hf.nx))
    grid_cnt = np.zeros((hf.ny, hf.nx))
    v = produced.vertices
    col = np.clip(((v[:, 0] - hf.x0) / hf.cell).astype(int), 0, hf.nx - 1)
    row = np.clip(((v[:, 1] - hf.y0) / hf.cell).astype(int), 0, hf.ny - 1)
    np.add.at(grid_sum, (row, col), abs_d)
    np.add.at(grid_cnt, (row, col), 1.0)
    with np.errstate(invalid="ignore"):
        dev_grid = np.where(grid_cnt > 0, grid_sum / np.maximum(grid_cnt, 1), np.nan)
    dev_grid[~hf.mask] = np.nan                     # NaN off the footprint

    fig, ax = plt.subplots(figsize=(5, 8))
    im = ax.imshow(dev_grid, origin="lower", cmap="viridis",
                   extent=[hf.x0, hf.x0 + hf.nx * hf.cell, hf.y0, hf.y0 + hf.ny * hf.cell])
    fig.colorbar(im, ax=ax, label="|deviation| (mm)")
    ax.set_title(f"{order_id} — {side} — deviation vs reference")
    fig.tight_layout()
    fig.savefig(png_path, dpi=110)
    plt.close(fig)


# ------------------------------------------------------------ per order -----

def run_order(order: dict, out_dir: Path) -> dict:
    """Replay one order and produce its scorecard dict (heatmap PNG written to disk).
    `diverges_from_norm` is filled in later by the aggregate pass."""
    tag = f"{order['order_id']}_{order['side']}"
    out_stl = str(out_dir / f"{tag}.stl")
    foot_rx = FootRx(**order["rx_for_side"])
    shell = Shell(**order["shell"])

    # run the engine verbatim — keep all six return values
    solid, aligned, hf, top, mask, report = generate_orthotic(
        order["scan_path"], order["side"], foot_rx, shell, out_stl)

    reference = trimesh.load(order["reference_path"], force="mesh")   # geometry only
    ref_aligned, rmse, align_fallback = _align_reference_to_produced(reference, solid)

    abs_d, dev_stats = _surface_deviation(ref_aligned, solid)
    hausdorff = _hausdorff(ref_aligned, solid)
    vol_delta, vol_wt = _volume_delta(ref_aligned, solid)
    cross_sections = _cross_sections(ref_aligned, solid, hf)
    landmark_deltas = _landmark_deltas(hf, top, ref_aligned)

    png_path = str(out_dir / f"{tag}_heatmap.png")
    _heatmap_png(hf, solid, abs_d, png_path, order["order_id"], order["side"])

    return {
        "order_id": order["order_id"], "side": order["side"],
        "mean_dev": dev_stats["mean_dev"], "p95": dev_stats["p95"],
        "max_dev": dev_stats["max_dev"], "signed": dev_stats["signed"],
        "hausdorff": hausdorff,
        "vol_delta_cm3": vol_delta, "volume_watertight": vol_wt,
        "rmse": round(float(rmse), 4), "align_fallback": align_fallback,
        "cross_sections": cross_sections, "landmark_deltas": landmark_deltas,
        "pii_scrubbed": order["pii_scrubbed"],
        "diverges_from_norm": False,                 # set by aggregate pass
        "heatmap_png": Path(png_path).name,
        "report": report,                            # engine provenance
    }


# ------------------------------------------------------------ aggregate -----

def _mad(x: np.ndarray) -> float:
    med = float(np.median(x))
    return float(np.median(np.abs(x - med)))


def replay(orders: list[dict], out_dir: Path, k_mad: float = 3.0) -> dict:
    """Replay all orders, write scorecards + summary, flag divergent orders."""
    out_dir.mkdir(parents=True, exist_ok=True)
    cards = [run_order(o, out_dir) for o in orders]

    # divergence: an order whose p95 or hausdorff exceeds median + k*MAD across the batch
    if len(cards) >= 2:
        p95 = np.array([c["p95"] for c in cards], float)
        haus = np.array([c["hausdorff"] for c in cards], float)
        p95_thr = float(np.median(p95)) + k_mad * _mad(p95)
        haus_thr = float(np.median(haus)) + k_mad * _mad(haus)
        for c in cards:
            c["diverges_from_norm"] = bool(c["p95"] > p95_thr or c["hausdorff"] > haus_thr)

    for c in cards:
        (out_dir / f"{c['order_id']}_{c['side']}_scorecard.json").write_text(
            json.dumps(c, indent=2))

    def agg(key):
        vals = np.array([c[key] for c in cards], float)
        return {"mean": round(float(vals.mean()), 4), "median": round(float(np.median(vals)), 4)}

    summary = {
        "n_orders": len(cards),
        "k_mad": k_mad,
        "aggregate": {k: agg(k) for k in ("mean_dev", "p95", "hausdorff")},
        "orders": [{k: c[k] for k in ("order_id", "side", "mean_dev", "p95", "hausdorff",
                                      "vol_delta_cm3", "rmse", "diverges_from_norm",
                                      "align_fallback", "pii_scrubbed")} for c in cards],
    }
    (out_dir / "replay_summary.json").write_text(json.dumps(summary, indent=2))
    return summary


# ------------------------------------------------ synthetic-pair generator --

def make_synthetic_orders(n: int, out_dir: Path, diverge_last: bool = False,
                          seed0: int = 100) -> list[Path]:
    """Fabricate N order dirs: a synthetic scan, its engine-produced part, and a
    reference = mildly-perturbed copy of that produced STL (deviations nonzero but
    bounded). Runs with no ANTHROPIC_API_KEY (Rx written as rx.json)."""
    from synthetic_foot import make_foot_scan
    out_dir.mkdir(parents=True, exist_ok=True)
    dirs = []
    for i in range(n):
        side = "right" if i % 2 == 0 else "left"
        seed = seed0 + i
        rng = np.random.default_rng(seed)
        order_id = f"SYNTH-{i:03d}"
        d = out_dir / f"order_{order_id}"
        d.mkdir(parents=True, exist_ok=True)

        scan = make_foot_scan(side=side, seed=seed)
        scan.export(str(d / "scan.obj"))

        rx = {"mods": [{"type": "medial_wedge", "degrees": 4.0}]}
        (d / "rx.json").write_text(json.dumps(rx))
        (d / "order.json").write_text(json.dumps({"order_id": order_id, "side": side}))

        # produce the part, then perturb it into the "accepted reference"
        produced, *_ = generate_orthotic(str(d / "scan.obj"), side,
                                         FootRx(**rx), Shell(), str(d / "_produced_tmp.stl"))
        ref = produced.copy()
        big = diverge_last and i == n - 1
        noise = 0.8 if big else 0.15            # mm vertex noise
        offset = 1.5 if big else 0.3            # mm rigid shift
        ref.vertices = ref.vertices + rng.normal(0, noise, ref.vertices.shape)
        ref.apply_translation(rng.normal(0, offset, 3))
        if big:
            ref.apply_scale(1.03)               # extra divergence for the UI badge demo
        ref.export(str(d / "reference.stl"))
        (d / "_produced_tmp.stl").unlink(missing_ok=True)
        dirs.append(d)
    return dirs


# --------------------------------------------------------------- selftest ---

def selftest() -> bool:
    """Identical reference==produced => near-zero deviation/hausdorff/volume-delta."""
    import tempfile
    from synthetic_foot import make_foot_scan
    tmp = Path(tempfile.mkdtemp())
    scan = make_foot_scan(side="right", seed=7)
    scan.export(str(tmp / "scan.obj"))
    rx = {"mods": []}
    produced, *_ = generate_orthotic(str(tmp / "scan.obj"), "right",
                                     FootRx(**rx), Shell(), str(tmp / "p.stl"))
    produced.export(str(tmp / "reference.stl"))          # reference == produced
    (tmp / "rx.json").write_text(json.dumps(rx))
    (tmp / "order.json").write_text(json.dumps({"order_id": "SELFTEST", "side": "right"}))
    card = run_order(load_order(tmp), tmp)
    ok = (card["mean_dev"] < 1e-3 and card["hausdorff"] < 1e-3 and abs(card["vol_delta_cm3"]) < 1e-2)
    print(f"selftest: mean_dev={card['mean_dev']} hausdorff={card['hausdorff']} "
          f"vol_delta_cm3={card['vol_delta_cm3']} -> {'PASS' if ok else 'FAIL'}")
    return ok


# -------------------------------------------------------------------- CLI ---

def main(argv=None):
    ap = argparse.ArgumentParser(description="Historical-order replay + diff scorecard")
    ap.add_argument("--synthetic", type=int, metavar="N", help="fabricate N synthetic order pairs")
    ap.add_argument("--diverge-last", action="store_true",
                    help="over-perturb the last synthetic order (for the divergence badge demo)")
    ap.add_argument("--orders", nargs="+", metavar="DIR", help="replay these order directories")
    ap.add_argument("--out", default="/tmp/replay_out", help="output directory")
    ap.add_argument("--k-mad", type=float, default=3.0, help="divergence threshold multiplier")
    ap.add_argument("--selftest", action="store_true", help="identical-mesh sanity check")
    args = ap.parse_args(argv)

    if args.selftest:
        return 0 if selftest() else 1

    out_dir = Path(args.out)
    if args.synthetic:
        synth_root = out_dir / "orders"
        dirs = make_synthetic_orders(args.synthetic, synth_root, diverge_last=args.diverge_last)
        orders = [load_order(d) for d in dirs]
    elif args.orders:
        orders = [load_order(d) for d in args.orders]
    else:
        ap.error("one of --synthetic, --orders, or --selftest is required")

    summary = replay(orders, out_dir, k_mad=args.k_mad)
    print(json.dumps(summary, indent=2))
    print(f"\nwrote {summary['n_orders']} scorecards + replay_summary.json to {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
