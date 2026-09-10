"""
eval/_make_fixtures.py — PLACEHOLDER fixture generator.

We could not run a live keyed model pass in this environment, so these fixtures
EMULATE realistic model output: most cases parse correctly, and three carry the
kind of error a real model plausibly makes (a unit slip, a missed needs_manual,
a partial fill). They are deliberately NOT verbatim copies of the labels — that
would make every case a trivial pass and measure nothing.

Replace them with real output when a key is available:
    python -m eval.score_rx --live --write-fixtures

Regenerate these placeholders:
    python -m eval._make_fixtures
"""
from __future__ import annotations
from pathlib import Path

from schema import (Prescription, FootRx, Fill, ZoneFill,
                    MedialWedge, HeelLift, Relief)
from eval.rx_dataset import get_cases

FIXTURES = Path(__file__).parent / "fixtures"

# case_id -> a function producing the emulated model Prescription (realistic error).
# Anything not listed is emulated as a correct parse (== expected), which is what a
# strong model returns for these unambiguous cases.
PERTURB = {
    # "1 cm" misread as 1 mm — a classic unit slip (param out of tolerance)
    "heel_lift_cm": lambda exp: exp.model_copy(deep=True, update={
        "left": FootRx(mods=[HeelLift(height_mm=1.0)])}),
    # model fails to flag the missing height; guesses a lift and does not set needs_manual
    "nm_ambiguous_lift": lambda exp: Prescription(
        order_id=exp.order_id, right=FootRx(mods=[HeelLift(height_mm=6.35)]),
        needs_manual=False),
    # model catches the softer heel but drops the "firmer forefoot" half (partial fill)
    "fill_soft_heel": lambda exp: exp.model_copy(deep=True, update={
        "left": FootRx(mods=[MedialWedge(degrees=4.0)],
                       fill=Fill(heel=ZoneFill(family="gyroid", density=0.3)))}),
}


def main():
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for c in get_cases():
        pred = PERTURB[c.case_id](c.expected) if c.case_id in PERTURB else c.expected.model_copy(deep=True)
        pred.order_id = c.case_id       # parse_prescription overwrites order_id authoritatively
        (FIXTURES / f"{c.case_id}.json").write_text(pred.model_dump_json(indent=2))
        tag = "PERTURBED" if c.case_id in PERTURB else "correct"
        print(f"  {c.case_id:<24} {tag}")
    print(f"wrote {len(get_cases())} fixtures to {FIXTURES}")


if __name__ == "__main__":
    main()
