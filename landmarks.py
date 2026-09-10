"""
landmarks.py — landmark-accuracy measurement, calibration, and an optional
(off-by-default, isolated) learned detector (batch-2 item 2).

Measures how far `Heightfield.landmark()`'s proportional guesses fall from
ground-truth landmark positions (mm), fits the row-fraction / column offset
against annotated data, and can write a `landmark_calibration.json` that opts
into the calibrated params. The default engine path is untouched: `geometry`'s
`LANDMARK_PARAMS` keep their byte-identical defaults until a caller explicitly
applies a calibration file.

Pure geometry — runs with NO `ANTHROPIC_API_KEY`. Importing this module never
pulls any ML package; the learned detector lazy-imports its deps inside methods.

CLI:
  python -m landmarks synth  annotations/            # write a synthetic annotation
  python -m landmarks measure annotations/           # per-landmark mm error table
  python -m landmarks calibrate annotations/         # fit + write landmark_calibration.json
"""
from __future__ import annotations
import argparse
import json
import sys
from copy import deepcopy
from pathlib import Path

import numpy as np
import trimesh

from geometry import (clean_scan, align_canonical, Heightfield,
                      LANDMARK_PARAMS, _col_from_rule)

LANDMARK_NAMES = ["heel_center", "base_5th_met", "met_heads", "arch_apex", "hallux"]
CALIB_PATH = Path("landmark_calibration.json")


# ------------------------------------------------------ grid <-> mm ---------

def grid_to_mm(hf: Heightfield, row: int, col: int) -> tuple[float, float]:
    """Aligned-frame plantar-plane mm for a grid index (row=j, col=i).
    Confirmed against Heightfield.__init__: world node = (x0 + i*cell, y0 + j*cell)."""
    return (hf.x0 + col * hf.cell, hf.y0 + row * hf.cell)


def _landmark_mm(hf: Heightfield, p: dict) -> tuple[float, float]:
    """(x_mm, y_mm) of a landmark given a single-landmark param dict {frac, col}."""
    row = int(hf.y_heel + p["frac"] * (hf.y_toe - hf.y_heel))     # row_at(frac)
    cols = np.where(hf.mask[row])[0]
    col = _col_from_rule(cols, p["col"])
    return grid_to_mm(hf, row, col)


# ------------------------------------------------------ annotations ---------

def load_annotation(path: str | Path) -> dict:
    """One JSON per aligned scan: {scan, side, landmarks:{name:[x_mm,y_mm], ...}}."""
    return json.loads(Path(path).read_text())


def _load_all(anno_dir: str | Path) -> list[dict]:
    d = Path(anno_dir)
    files = [d] if d.is_file() else sorted(d.glob("*.json"))
    return [load_annotation(f) for f in files]


def _hf_for(annotation: dict, use_icp: bool = True) -> Heightfield:
    """Mirror generate_orthotic preprocessing so measured error reflects production."""
    scan = trimesh.load(annotation["scan"], force="mesh")
    scan = clean_scan(scan)
    aligned, _ = align_canonical(scan, annotation["side"], use_icp=use_icp)
    return Heightfield(aligned)


# ------------------------------------------------------ measurement ---------

def _measure_data(data: list[tuple], params: dict) -> dict:
    """Per-landmark euclidean error (mm) over precomputed (hf, ground_truth) pairs."""
    errs = {n: [] for n in LANDMARK_NAMES}
    for hf, gts in data:
        for n in LANDMARK_NAMES:
            if n not in gts:
                continue
            gx, gy = gts[n]
            px, py = _landmark_mm(hf, params[n])
            errs[n].append(float(np.hypot(px - gx, py - gy)))
    out = {}
    for n in LANDMARK_NAMES:
        e = np.array(errs[n], float)
        if len(e) == 0:
            out[n] = {"n": 0, "mean_mm": None, "p95_mm": None, "max_mm": None}
        else:
            out[n] = {"n": int(len(e)), "mean_mm": round(float(e.mean()), 3),
                      "p95_mm": round(float(np.percentile(e, 95)), 3),
                      "max_mm": round(float(e.max()), 3)}
    return out


def measure(annotations: list[dict], use_icp: bool = True, params: dict | None = None) -> dict:
    """Per-landmark mm error (mean/p95/max) for a set of annotations. No API key needed.

    Returns a plain dict so a scorecard can `.update()` it in (mirroring how
    validate's report is merged in generate_orthotic). NOTE: replay.py (item 1)
    computes produced-vs-reference landmark deltas, a different quantity; this is
    heuristic-vs-ground-truth accuracy, exposed as a dict for future wiring.
    """
    params = params or LANDMARK_PARAMS
    data = [(_hf_for(a, use_icp), a["landmarks"]) for a in annotations]
    return _measure_data(data, params)


# ------------------------------------------------------ calibration ---------

def calibrate(annotations: list[dict], use_icp: bool = True,
              write: bool = True) -> tuple[dict, dict, dict]:
    """Fit each landmark's row-fraction + column offset to the ground truth.

    Brute-force over a grid that INCLUDES the current defaults, minimizing mean
    euclidean error per landmark — so calibrated error is <= default error on the
    fit set by construction. Returns (calibrated_params, before_errors, after_errors)
    and writes landmark_calibration.json when `write`.
    """
    data = [(_hf_for(a, use_icp), a["landmarks"]) for a in annotations]
    before = _measure_data(data, LANDMARK_PARAMS)
    calibrated = deepcopy(LANDMARK_PARAMS)

    for n in LANDMARK_NAMES:
        default = LANDMARK_PARAMS[n]
        gt = [(hf, gts[n]) for hf, gts in data if n in gts]
        if not gt:
            continue
        fracs = sorted(set(np.clip(
            np.linspace(default["frac"] - 0.15, default["frac"] + 0.15, 61), 0, 1).tolist()
            + [default["frac"]]))
        adds = list(range(-8, 9)) + [default["col"].get("add", 0)]
        best, best_err = None, np.inf
        for fr in fracs:
            for ad in adds:
                rule = dict(default["col"]); rule["add"] = ad
                p = {"frac": float(fr), "col": rule}
                e = np.mean([np.hypot(*(np.subtract(_landmark_mm(hf, p), g))) for hf, g in gt])
                if e < best_err:
                    best_err, best = e, p
        calibrated[n] = best

    after = _measure_data(data, calibrated)
    if write:
        CALIB_PATH.write_text(json.dumps(calibrated, indent=2))
    return calibrated, before, after


def load_calibration(path: str | Path = CALIB_PATH) -> dict:
    """Load a calibration file (calibrated LANDMARK_PARAMS)."""
    return json.loads(Path(path).read_text())


def apply_calibration(path: str | Path = CALIB_PATH) -> None:
    """Opt in: overwrite geometry.LANDMARK_PARAMS in place with calibrated values.
    Explicit and never called by the default engine path — this is the opt-in seam."""
    import geometry
    geometry.LANDMARK_PARAMS.clear()
    geometry.LANDMARK_PARAMS.update(load_calibration(path))


# ------------------------------- OPTIONAL learned detector (isolated) -------

class LearnedLandmarkDetector:
    """Optional learned landmark detector. OFF by default and fully isolated:
    every ML import is lazy inside a method, so importing landmarks.py never pulls
    scikit-learn (or any ML dep). Enable explicitly via detector="learned".

    Features are the aligned Heightfield.Z / mask arrays; targets are landmark
    (x_mm, y_mm). This is a scaffold — the heuristic path never instantiates it.
    """

    def __init__(self):
        self._models = {}

    @staticmethod
    def _features(hf: Heightfield) -> np.ndarray:
        z = np.where(hf.mask, hf.Z, 0.0)
        # coarse fixed-size descriptor so scans of differing grid size compare
        from numpy import interp  # stdlib-numpy only for the descriptor
        flat = z[hf.mask]
        return np.array([flat.mean(), flat.std(), flat.max(), hf.L,
                         float(hf.mask.sum())]) if flat.size else np.zeros(5)

    def fit(self, samples):
        # lazy ML import — keeps landmarks.py importable with no ML installed
        try:
            from sklearn.linear_model import Ridge  # optional extra; see requirements.txt
        except ImportError as e:
            raise RuntimeError("LearnedLandmarkDetector requires scikit-learn "
                               "(optional extra; not a core dependency)") from e
        X = np.array([self._features(hf) for hf, _ in samples])
        for n in LANDMARK_NAMES:
            Y = np.array([gts[n] for _, gts in samples if n in gts])
            if len(Y) == len(X) and len(X) > 1:
                self._models[n] = Ridge().fit(X, Y)
        return self

    def predict(self, hf: Heightfield) -> dict:
        if not self._models:
            raise RuntimeError("LearnedLandmarkDetector not fitted")
        x = self._features(hf)[None, :]
        return {n: tuple(map(float, m.predict(x)[0])) for n, m in self._models.items()}


# ------------------------------------------------------ synthetic anno ------

def write_synthetic_annotation(anno_dir: str | Path, side: str = "right", seed: int = 7) -> Path:
    """Emit a synthetic annotation whose ground truth == the current proportional
    landmark positions, so measured error should be ≈0 (validates grid↔mm)."""
    from synthetic_foot import make_foot_scan
    d = Path(anno_dir); d.mkdir(parents=True, exist_ok=True)
    scan_path = d / f"synth_scan_{side}_{seed}.obj"
    make_foot_scan(side=side, seed=seed).export(str(scan_path))
    hf = _hf_for({"scan": str(scan_path), "side": side})
    landmarks = {n: list(grid_to_mm(hf, *hf.landmark(n))) for n in LANDMARK_NAMES}
    anno = {"scan": str(scan_path), "side": side, "landmarks": landmarks}
    out = d / f"synth_{side}_{seed}.json"
    out.write_text(json.dumps(anno, indent=2))
    return out


# -------------------------------------------------------------------- CLI ---

def _print_table(title: str, errs: dict):
    print(title)
    print(f"  {'landmark':<14} {'n':>3} {'mean':>8} {'p95':>8} {'max':>8}  (mm)")
    for n in LANDMARK_NAMES:
        e = errs[n]
        print(f"  {n:<14} {e['n']:>3} {str(e['mean_mm']):>8} {str(e['p95_mm']):>8} {str(e['max_mm']):>8}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Landmark accuracy measurement + calibration")
    ap.add_argument("cmd", choices=["measure", "calibrate", "synth"])
    ap.add_argument("anno_dir", help="directory of annotation JSONs (or a single JSON)")
    ap.add_argument("--no-icp", action="store_true", help="disable ICP in alignment")
    args = ap.parse_args(argv)
    use_icp = not args.no_icp

    if args.cmd == "synth":
        p = write_synthetic_annotation(args.anno_dir)
        print(f"wrote synthetic annotation -> {p}")
        return 0

    annotations = _load_all(args.anno_dir)
    if not annotations:
        ap.error(f"no annotation JSONs found in {args.anno_dir}")

    if args.cmd == "measure":
        _print_table(f"Landmark error over {len(annotations)} annotation(s):",
                     measure(annotations, use_icp=use_icp))
        return 0

    if args.cmd == "calibrate":
        _, before, after = calibrate(annotations, use_icp=use_icp, write=True)
        _print_table("BEFORE (defaults):", before)
        _print_table("AFTER  (calibrated):", after)
        regressed = [n for n in LANDMARK_NAMES
                     if before[n]["mean_mm"] is not None
                     and after[n]["mean_mm"] > before[n]["mean_mm"] + 1e-9]
        print(f"\nwrote {CALIB_PATH}. regressed landmarks: {regressed or 'none'}")
        return 1 if regressed else 0


if __name__ == "__main__":
    sys.exit(main())
