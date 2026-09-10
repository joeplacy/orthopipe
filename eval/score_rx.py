"""
eval/score_rx.py — Rx parser evaluation harness (batch-2 item 3).

Scores `parse_prescription` (or cached fixtures) over the labeled cases in
`rx_dataset.py`: per-mod-type precision/recall, param-within-tolerance, side
assignment, unit conversion (surfaces as a param miss), and `needs_manual`
firing — plus a confusion list of failures.

Run modes:
  python -m eval.score_rx --offline               # default; uses eval/fixtures/, NO key,
                                                   #   never imports rx_parser / anthropic
  python -m eval.score_rx --live [--write-fixtures]  # calls the real parser (needs key)
  python -m eval.score_rx --selfcheck             # in-memory scoring self-test, no fixtures

The harness only READS schema models; it never imports geometry or mutates schema.
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

from schema import Prescription
from eval.rx_dataset import get_cases

FIXTURES = Path(__file__).parent / "fixtures"

# tolerances (tunable module constants)
DEG_TOL = 0.5
MM_TOL = 1.0
DENSITY_TOL = 0.1

# numeric fields to compare per mod type (landmark handled as exact-match separately)
TYPE_FIELDS = {
    "medial_wedge": [("degrees", DEG_TOL)],
    "lateral_wedge": [("degrees", DEG_TOL)],
    "heel_lift": [("height_mm", MM_TOL)],
    "met_pad": [("height_mm", MM_TOL)],
    "relief": [("depth_mm", MM_TOL), ("radius_mm", MM_TOL)],
}
MOD_TYPES = list(TYPE_FIELDS)


def match_mods(exp_mods, pred_mods):
    """Greedy one-to-one same-type match. Yields (expected|None, predicted|None)."""
    pred = list(pred_mods)
    for e in exp_mods:
        j = next((k for k, p in enumerate(pred) if p.type == e.type), None)
        yield e, (pred.pop(j) if j is not None else None)   # None => FN
    for p in pred:
        yield None, p                                        # leftover => FP


def _param_check(e, p):
    """(ok_count, total, [failure_strings]) for one matched expected/predicted pair."""
    ok, total, fails = 0, 0, []
    for field, tol in TYPE_FIELDS[e.type]:
        total += 1
        ev, pv = getattr(e, field), getattr(p, field)
        if abs(float(ev) - float(pv)) <= tol:
            ok += 1
        else:
            fails.append(f"{e.type}.{field} expected {ev} got {pv} (tol {tol})")
    if e.type == "relief":                      # landmark is exact-match, not tolerance
        total += 1
        if e.landmark == p.landmark:
            ok += 1
        else:
            fails.append(f"relief.landmark expected {e.landmark} got {p.landmark}")
    return ok, total, fails


def score_case(expected: Prescription, pred: Prescription, acc: dict) -> dict:
    """Score one case, updating aggregate accumulators `acc`. Returns per-case detail."""
    failures = []
    side_ok = True

    for side in ("left", "right"):
        e_foot, p_foot = getattr(expected, side), getattr(pred, side)
        if (e_foot is None) != (p_foot is None):
            side_ok = False
            failures.append(f"side:{side} expected_present={e_foot is not None} "
                            f"got_present={p_foot is not None}")
            continue
        if e_foot is None:
            continue
        # mod presence + params
        for e_mod, p_mod in match_mods(e_foot.mods, p_foot.mods):
            if e_mod is not None and p_mod is not None:
                acc["tp"][e_mod.type] = acc["tp"].get(e_mod.type, 0) + 1
                ok, total, pfails = _param_check(e_mod, p_mod)
                acc["param_ok"] += ok
                acc["param_total"] += total
                for f in pfails:
                    failures.append(f"{side}:{f}")
            elif e_mod is not None:             # FN: expected mod missing
                acc["fn"][e_mod.type] = acc["fn"].get(e_mod.type, 0) + 1
                failures.append(f"{side}:missing {e_mod.type}")
            else:                               # FP: predicted an unexpected mod
                acc["fp"][p_mod.type] = acc["fp"].get(p_mod.type, 0) + 1
                failures.append(f"{side}:extra {p_mod.type}")
        # fill (light, secondary)
        if e_foot.fill is not None:
            if p_foot.fill is None:
                failures.append(f"{side}:fill expected but missing")
            else:
                for zone in ("heel", "midfoot", "forefoot", "toe"):
                    ez, pz = getattr(e_foot.fill, zone), getattr(p_foot.fill, zone)
                    if ez.family != pz.family or abs(ez.density - pz.density) > DENSITY_TOL:
                        failures.append(f"{side}:fill.{zone} expected "
                                        f"{ez.family}/{ez.density} got {pz.family}/{pz.density}")

    acc["side_total"] += 1
    if side_ok:
        acc["side_ok"] += 1

    # needs_manual 2x2
    e_nm, p_nm = bool(expected.needs_manual), bool(pred.needs_manual)
    if e_nm and p_nm:
        acc["nm"]["tp"] += 1
    elif e_nm and not p_nm:
        acc["nm"]["fn"] += 1
        failures.append("needs_manual expected True got False")
    elif not e_nm and p_nm:
        acc["nm"]["fp"] += 1
        failures.append("needs_manual expected False got True")
    else:
        acc["nm"]["tn"] += 1

    return {"side_ok": side_ok, "failures": failures}


def _new_acc():
    return {"tp": {}, "fp": {}, "fn": {}, "param_ok": 0, "param_total": 0,
            "side_ok": 0, "side_total": 0,
            "nm": {"tp": 0, "fp": 0, "fn": 0, "tn": 0}}


def _pr(tp, fp, fn):
    prec = tp / (tp + fp) if (tp + fp) else 1.0
    rec = tp / (tp + fn) if (tp + fn) else 1.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    return round(prec, 3), round(rec, 3), round(f1, 3)


def report(acc: dict, per_case: list[dict]) -> dict:
    """Print summary + confusion list; return a machine-readable summary dict."""
    print("=" * 68)
    print("Rx PARSER EVAL SUMMARY")
    print("=" * 68)
    n_cases = len(per_case)
    passed = sum(1 for c in per_case if not c["detail"]["failures"])
    print(f"cases: {n_cases}   full-pass: {passed}   pass-rate: {passed / n_cases:.1%}")
    print(f"side assignment accuracy: {acc['side_ok']}/{acc['side_total']} "
          f"= {acc['side_ok'] / max(acc['side_total'], 1):.1%}")
    pt = acc["param_total"]
    print(f"param-within-tolerance: {acc['param_ok']}/{pt} "
          f"= {(acc['param_ok'] / pt) if pt else 1.0:.1%}")

    print("\nper-mod-type precision/recall/F1:")
    micro_tp = micro_fp = micro_fn = 0
    for t in MOD_TYPES:
        tp, fp, fn = acc["tp"].get(t, 0), acc["fp"].get(t, 0), acc["fn"].get(t, 0)
        micro_tp += tp; micro_fp += fp; micro_fn += fn
        if tp + fp + fn == 0:
            continue
        p, r, f = _pr(tp, fp, fn)
        print(f"  {t:<14} TP={tp} FP={fp} FN={fn}   P={p} R={r} F1={f}")
    mp, mr, mf = _pr(micro_tp, micro_fp, micro_fn)
    print(f"  {'MICRO':<14} TP={micro_tp} FP={micro_fp} FN={micro_fn}   P={mp} R={mr} F1={mf}")

    nm = acc["nm"]
    nmp, nmr, _ = _pr(nm["tp"], nm["fp"], nm["fn"])
    fired = nm["tp"] + nm["fp"]
    print(f"\nneeds_manual: P={nmp} R={nmr}  fired={fired}/{n_cases} "
          f"(tp={nm['tp']} fp={nm['fp']} fn={nm['fn']} tn={nm['tn']})")

    fails = [c for c in per_case if c["detail"]["failures"]]
    print(f"\nCONFUSION LIST ({len(fails)} failing case(s)):")
    for c in fails:
        print(f"  [{c['case_id']}] " + "; ".join(c["detail"]["failures"]))
    if not fails:
        print("  (none)")

    return {
        "n_cases": n_cases, "full_pass": passed, "pass_rate": round(passed / n_cases, 4),
        "side_accuracy": round(acc["side_ok"] / max(acc["side_total"], 1), 4),
        "param_accuracy": round((acc["param_ok"] / pt) if pt else 1.0, 4),
        "mod_micro": {"precision": mp, "recall": mr, "f1": mf},
        "needs_manual": {"precision": nmp, "recall": nmr, "fired": fired},
        "failures": [c["case_id"] for c in fails],
    }


# ------------------------------------------------------------ run modes -----

def _predict_offline(cases):
    """Load cached fixtures; skip (don't fail) cases with no fixture. Imports only schema."""
    preds, skipped = {}, []
    for c in cases:
        fx = FIXTURES / f"{c.case_id}.json"
        if not fx.exists():
            skipped.append(c.case_id)
            continue
        preds[c.case_id] = Prescription.model_validate_json(fx.read_text())
    return preds, skipped


def _predict_live(cases, write_fixtures=False, backend=None):
    """Call the real parser over `backend`. Lazy-imports rx_parser HERE ONLY so
    --offline never pulls anthropic. `backend=None` uses the parser's default."""
    from rx_parser import parse_prescription   # in-branch lazy import (pulls anthropic)
    preds = {}
    for c in cases:
        pred = parse_prescription(c.rx_text, order_id=c.case_id, backend=backend)
        preds[c.case_id] = pred
        if write_fixtures:
            FIXTURES.mkdir(parents=True, exist_ok=True)
            (FIXTURES / f"{c.case_id}.json").write_text(pred.model_dump_json(indent=2))
    return preds, []


def run(offline=True, write_fixtures=False, backend=None):
    cases = get_cases()
    preds, skipped = (_predict_offline(cases) if offline
                      else _predict_live(cases, write_fixtures, backend))
    acc = _new_acc()
    per_case = []
    for c in cases:
        if c.case_id not in preds:
            continue
        detail = score_case(c.expected, preds[c.case_id], acc)
        per_case.append({"case_id": c.case_id, "detail": detail})
    summary = report(acc, per_case)
    if skipped:
        print(f"\nskipped {len(skipped)} case(s) with no fixture: {skipped}")
    summary["skipped"] = skipped
    return summary


# ------------------------------------------------------------ self-check ----

def selfcheck() -> bool:
    """CI-testable scoring logic proof on in-memory pairs — no fixtures, no model call."""
    from schema import FootRx, MedialWedge, HeelLift, Prescription as P
    ok = True

    # 1) identical exp==pred => full pass
    exp = P(order_id="x", left=FootRx(mods=[MedialWedge(degrees=4.0), HeelLift(height_mm=6.0)]))
    acc = _new_acc()
    d = score_case(exp, exp.model_copy(deep=True), acc)
    ok &= (not d["failures"])
    print(f"selfcheck identical -> full pass: {not d['failures']}")

    # 2) heel_lift out of tolerance => param failure flagged
    bad = P(order_id="x", left=FootRx(mods=[MedialWedge(degrees=4.0), HeelLift(height_mm=9.0)]))
    d2 = score_case(exp, bad, _new_acc())
    hit = any("height_mm" in f for f in d2["failures"])
    ok &= hit
    print(f"selfcheck param-out-of-tol flagged: {hit}  ({d2['failures']})")

    # 3) needs_manual mismatch flagged
    e3 = P(order_id="x", needs_manual=True)
    p3 = P(order_id="x", needs_manual=False)
    d3 = score_case(e3, p3, _new_acc())
    nmhit = any("needs_manual" in f for f in d3["failures"])
    ok &= nmhit
    print(f"selfcheck needs_manual mismatch flagged: {nmhit}")

    # 4) side assignment error flagged
    e4 = P(order_id="x", left=FootRx(mods=[MedialWedge(degrees=4.0)]))
    p4 = P(order_id="x", right=FootRx(mods=[MedialWedge(degrees=4.0)]))
    d4 = score_case(e4, p4, _new_acc())
    sidehit = (not d4["side_ok"]) and any("side:" in f for f in d4["failures"])
    ok &= sidehit
    print(f"selfcheck side error flagged: {sidehit}")

    print(f"\nSELFCHECK {'PASS' if ok else 'FAIL'}")
    return ok


def main(argv=None):
    ap = argparse.ArgumentParser(description="Rx parser evaluation harness")
    ap.add_argument("--offline", action="store_true", help="use cached fixtures (default, no key)")
    ap.add_argument("--live", action="store_true", help="call the real parser (needs ANTHROPIC_API_KEY)")
    ap.add_argument("--write-fixtures", action="store_true", help="(with --live) refresh fixtures")
    ap.add_argument("--selfcheck", action="store_true", help="in-memory scoring self-test")
    ap.add_argument("--backend", default=None,
                    help="(with --live) Rx backend to parametrize over: anthropic|local")
    ap.add_argument("--min-pass-rate", type=float, default=None,
                    help="exit non-zero if full-pass rate falls below this (CI gate)")
    args = ap.parse_args(argv)

    if args.selfcheck:
        return 0 if selfcheck() else 1

    offline = not args.live       # offline is the default
    summary = run(offline=offline, write_fixtures=args.write_fixtures, backend=args.backend)
    if args.min_pass_rate is not None and summary["pass_rate"] < args.min_pass_rate:
        print(f"\nFAIL: pass-rate {summary['pass_rate']} < --min-pass-rate {args.min_pass_rate}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
