# Rx parser evaluation harness

Scores `rx_parser.parse_prescription` against labeled `(rx_text, expected Prescription)`
cases: per-mod-type precision/recall, param-within-tolerance, side assignment, unit
conversion (surfaces as a param miss), and `needs_manual` firing — plus a confusion
list of failures.

## Run

```bash
# CI path — offline, NO ANTHROPIC_API_KEY, uses eval/fixtures/. Never imports rx_parser/anthropic.
python -m eval.score_rx --offline

# scoring-logic self-test (in-memory, no fixtures, no model call)
python -m eval.score_rx --selfcheck

# live — calls the real parser (needs ANTHROPIC_API_KEY); refresh cached fixtures
python -m eval.score_rx --live --write-fixtures

# CI gate: fail if the full-pass rate regresses
python -m eval.score_rx --offline --min-pass-rate 0.75
```

## Adding real cases

Append `Case(case_id, rx_text, expected, note)` rows to `eval/rx_dataset.py`, building
`expected` from real schema objects. Then capture a fixture from **real model output**
via `--live --write-fixtures` (or hand-author it from real output). Never seed a fixture
from `expected` — a copied label trivially full-passes and measures nothing.

## Fixtures status

⚠️ **The committed `eval/fixtures/*.json` are PLACEHOLDER emulations**, not a live keyed
run — this environment had no `ANTHROPIC_API_KEY`. Most emulate a correct parse (what a
strong model returns for these unambiguous cases); three carry a realistic model error
(a unit slip, a missed `needs_manual`, a partial fill) so the harness measures something
non-trivial. Regenerate them from real output with `--live --write-fixtures` (or
`python -m eval._make_fixtures` to rebuild the placeholders).

## Last measured accuracy (placeholder fixtures, 16 cases)

| metric | value |
|---|---|
| full-pass rate | 81.2% (13/16) |
| side-assignment accuracy | 93.8% |
| param-within-tolerance | 96.3% |
| mod micro P / R / F1 | 1.0 / 1.0 / 1.0 |
| needs_manual P / R | 1.0 / 0.667 |
| needs_manual fired-rate | 2/16 |

Per-mod-type: medial_wedge, lateral_wedge, heel_lift, met_pad, relief all P=R=F1=1.0
on the placeholder set. Confusion list: `heel_lift_cm` (unit slip), `fill_soft_heel`
(dropped forefoot zone), `nm_ambiguous_lift` (missed `needs_manual`). **Replace these
numbers with a real `--live` run before quoting accuracy.**

Tolerances (tunable in `score_rx.py`): `DEG_TOL=0.5`, `MM_TOL=1.0`, `DENSITY_TOL=0.1`.
