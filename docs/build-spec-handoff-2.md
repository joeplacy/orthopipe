# OrthoPipe — Implementation Handoff #2

## You are working on

**OrthoPipe** is a pipeline that turns a 3D foot scan (`.obj`) plus a clinician prescription into a validated, 3D-print-ready orthotic STL.

**Stack:** Python, `trimesh`, `pymeshlab`, `pydantic`, `FastAPI`.

**Key files:**
- `schema.py` — Pydantic Rx contract (the LLM↔geometry interface), now including optional per-zone `fill`.
- `geometry.py` — the engine: cleanup → align → heightfield → shell → mods → validate → export.
- `rx_parser.py` — Claude-powered free-text Rx → `Prescription`.
- `app.py` — FastAPI review UI + audit log.
- `vendor/` — MIT-licensed ampscan ICP.
- `templates/` — canonical foot template.

**Dev loop:**
```bash
source .venv/bin/activate
python run_demo.py   # end-to-end geometry check (must keep passing), ~12s
python app.py        # serves review UI on :8000; copies index.html -> static/index.html
```

## This is handoff #2

A **first handoff** already specced: mesh-cleanup, ICP alignment, the lattice/fill schema, a lattice generator, and the audit/complaint log.

Before starting any item here, **VERIFY the state of that prior work directly in the code.** Do **not** re-implement anything already done. Where an item below depends on a batch-1 deliverable and that deliverable is incomplete or absent, implement against the **current reality of the repo** and **note the assumption inline** in your code/comments. (Several sections already flag likely gaps — e.g. lattice may be absent, eval modules may be absent — treat those flags as hypotheses to confirm, not facts.)

## How to work

- **Smallest change per spec.** No unrelated refactors, no drive-by cleanups.
- **Preserve public signatures and return shapes.** In particular: `generate_orthotic`'s 6-tuple return `(solid, aligned, hf, top, mask, report)`, and `validate`'s existing report keys (`pass`, `checks`, `volume_cm3`, `extents_mm`, `est_print_mass_g_tpu`, plus the `.update()`-ed `side`/`mods_applied`/`warnings`/`foot_length_mm`/`output`).
- **After each item, run `python run_demo.py`** and confirm `report["pass"] is True` (watertight, positive volume, plausible dims) before moving to the next.
- **Match existing code style.**
- **Any new dependency goes in `requirements.txt` AND must be optional/light.** These items must not make the core pipeline heavier to import — optional deps are lazy-imported and degrade gracefully when absent.
- **Prefer tools that run with NO `ANTHROPIC_API_KEY`** so CI can exercise them.
- **Ask nothing.** Implement to spec; when the spec says "confirm in code," confirm it; where you must assume, note the assumption inline.

## One-line map

Item **1 (replay + diff harness)** is the keystone that unblocks calibration. Items **2–3 (landmark accuracy, Rx eval)** are the calibration/measurement layer that feeds it. Item **4 (pressure validation)** makes `pass` clinically meaningful. Item **5 (local-LLM fallback)** de-risks PHI. Item **6 (phone-scan)** is GTM-facing and independent. **Do item 1 first; items 2–4 build on it.**

---

# 1. Historical-order replay + geometric diff scorecard

**Objective.** Add a standalone `replay.py` module + CLI that runs a directory of historical orders (scan + Rx + accepted reference STL) through `generate_orthotic`, aligns each produced STL to its reference, and emits a per-order geometric-diff scorecard (surface deviation stats, deviation heatmap PNG, volume delta, clinical cross-sections, landmark deltas) plus an aggregate summary — with a synthetic-pair generator so it runs today against zero real data.

**Why it matters.** Turns the engine from "produces a plausible part" into "measurably close to accepted clinical output," unblocking all downstream calibration.

**Files to touch**
- `/Users/sam/Documents/Claude/Orthopipe/replay.py` — **NEW** (module + `if __name__ == "__main__"` CLI).
- `/Users/sam/Documents/Claude/Orthopipe/synthetic_foot.py` — extend (or add a sibling helper in `replay.py`) to emit a perturbed "reference" STL. The current entrypoint is `make_foot_scan(length_mm, width_mm, side, ...)` — call it by that signature.
- `/Users/sam/Documents/Claude/Orthopipe/geometry.py` — **READ ONLY**, reuse `generate_orthotic`, `align_canonical`, `refine_icp`, `Heightfield`. Do not modify.
- `/Users/sam/Documents/Claude/Orthopipe/static/index.html` + `/Users/sam/Documents/Claude/Orthopipe/app.py` — **NEW** small additive surface: one read endpoint + a heatmap/"diverges from historical norm" panel. Keep existing routes/response shapes intact.

**Exact approach**

1. **Order ingestion.** Define an order as a directory containing: `scan.obj` (or `.stl`/`.ply` — the loader path already tolerates these), a reference STL (the accepted/fabricator part, e.g. `reference.stl`), and the driving Rx as `rx.json` (a serialized `Prescription` or per-foot `FootRx`) and/or `rx_text`. Read `side` and `order_id` from a small `order.json` (or infer `order_id` from the directory name; require explicit `side ∈ {"left","right"}`). If only `rx_text` is present and no `rx.json`, treat parsing as optional — see guardrail on `ANTHROPIC_API_KEY`.

2. **De-identification guard.** Before doing anything, scrub/refuse PII. Operate on geometry only: load meshes with `trimesh.load(..., force="mesh")` and never propagate mesh `metadata`. For the JSON sidecars, drop any key matching an obvious-PII denylist (`name`, `dob`, `mrn`, `patient`, `ssn`, `address`, `phone`, `email`, case-insensitive) and, if such keys are found, record `"pii_scrubbed": [<keys>]` in the per-order output rather than failing silently. Only `order_id`, `side`, geometry, and clinical Rx fields survive downstream.

3. **Run the engine.** For each order, build `FootRx(**rx_for_side)` and `Shell(**shell_dict)` (defaulting to `Shell()`), then call the existing entrypoint verbatim:
   ```python
   solid, aligned, hf, top, mask, report = generate_orthotic(scan_path, side, foot_rx, shell, out_stl)
   ```
   Keep all six return values; you need `solid` (produced part), `hf` (for landmark rows/`cell`/`L`), and `report` (surface into the scorecard). Write the produced STL to an output dir keyed by `order_id_side`.

4. **Align produced → reference.** Load the reference STL. Reuse the alignment path: since `generate_orthotic` already runs `align_canonical(..., use_icp=True)` on the *scan*, the produced part is in canonical frame but the reference may not be. Rigid-align the reference to the produced part (or vice versa) using the vendored ICP the repo already imports — `from vendor.ampscan_icp import register_icp` — mirroring how `refine_icp(mesh, template)` calls `register_icp(mesh.vertices, mesh.faces, template.vertices, template.faces)` and returns `(transformed_mesh, rmse)`. If ICP fails or residual is implausible, fall back to a coarse rigid PCA/centroid align and record `"align_fallback": true`. Record the final alignment `rmse`.

5. **Surface-deviation scorecard.** With reference mesh `A` and produced mesh `B` (post-align), compute per-vertex signed deviation and stats:
   ```python
   d = trimesh.proximity.signed_distance(A, B.vertices)   # (len(B.vertices),) mm, signed
   ad = np.abs(d)
   mean_dev, p95 = float(ad.mean()), float(np.percentile(ad, 95))
   ```
   Hausdorff (trimesh has no builtin — compute both one-sided directions via `trimesh.proximity.closest_point` and take the max):
   ```python
   def one_sided(src, dst):
       _, dist, _ = trimesh.proximity.closest_point(dst, src.vertices)
       return float(dist.max())
   hausdorff = max(one_sided(A, B), one_sided(B, A))
   ```
   `rtree`/`manifold3d` are already hard deps in `requirements.txt`, so the watertight signed-distance path works out of the box. Keep a defensive fallback anyway: if `signed_distance` is unavailable, fall back to unsigned `closest_point(A, B.vertices)` distances for the heatmap and stats and set `"signed": false`. Sample the surface for a denser Hausdorff only if needed (`trimesh.sample.sample_surface`).

6. **Deviation heatmap PNG.** Reuse `run_demo.py`'s **exact** preview-render path, which is **matplotlib Agg** (`matplotlib.use("Agg")`, `ax.imshow(...)`, `fig.savefig(...)`). There is **no 3D mesh renderer** in the stack, and `trimesh.Scene.save_image` needs pyglet/OpenGL, which is **not** in `requirements.txt` and won't run headless — do **NOT** use `scene().save_image` or `visual.vertex_colors`. Instead, rasterize per-vertex `abs(d)` onto the `Heightfield` grid (bin each produced-vertex XY into a `(hf.ny, hf.nx)` array — take mean/max `abs(d)` per cell, NaN off-mask — or use a 2D scatter of vertex XY colored by `abs(d)`), then render with matplotlib:
   ```python
   import matplotlib
   matplotlib.use("Agg")
   import matplotlib.pyplot as plt
   fig, ax = plt.subplots()
   im = ax.imshow(dev_grid, cmap="viridis")   # dev_grid = abs(d) rasterized onto the hf grid, NaN off-mask
   fig.colorbar(im, ax=ax, label="|deviation| (mm)")
   fig.savefig(png_path)
   plt.close(fig)
   ```
   Confirm the exact `matplotlib` calls `run_demo.py` uses for its preview PNGs and mirror them so the two render paths stay consistent.

7. **Volume delta.** Use the `volume` property, guarded by watertightness (both `generate_orthotic`'s `validate` and the produced solid already care about this):
   ```python
   vol_delta_cm3 = round((B.volume - A.volume) / 1000.0, 2)  # +added / -removed material
   ```
   Only trust the sign if `A.is_watertight and B.is_watertight`; otherwise report magnitude with `"volume_watertight": false`.

8. **Clinical cross-sections** via planar sections on the produced part, using `Heightfield` landmark rows to place the planes. `hf.landmark(name)` returns `(row, col)` in grid units; convert a row to a world Y with `hf` geometry (`cell = CELL_MM = 2.0`, heel at `y=0`; confirm exact `y_heel`/`cell` world mapping in `Heightfield.__init__`). Then:
   - **Arch height:** transverse-ish section through `landmark("arch_apex")`; take the section's vertical span. Use `mesh.section_multiplane(origin, normal, heights)` for efficiency and read each `Path2D.extents[1]` (vertical bbox span) as the height metric.
   - **Heel-cup depth:** section through `landmark("heel_center")`; depth = rim height minus cup-floor along the section's vertical extent (from `Path2D.bounds`).
   - **Rearfoot / forefoot posting angle:** fit a line to the bottom (z≈0 posting face) of a heel-region section vs a forefoot-region section and report the tilt in degrees. Compute the same metrics on `A` and `B` and emit deltas.
   Cross-section snippet:
   ```python
   secs = mesh.section_multiplane(plane_origin, plane_normal=[0,1,0], heights=[...])  # list[Path2D|None]
   ```
   Skip gracefully (`None` section) if a plane misses the mesh.

9. **Landmark-position deltas.** For each accepted name (`heel_center`, `base_5th_met`, `met_heads`, `arch_apex`, `hallux`), map `hf.landmark(name)` `(row,col)` to a world XY on the produced part, find the nearest reference-surface point via `trimesh.proximity.closest_point`, and report the 3D delta magnitude per landmark.

10. **Emit outputs.** Per order: `<order_id>_<side>_scorecard.json` (all stats above + `rmse`, `pii_scrubbed`, feature-availability flags + the engine's own `report` dict embedded for provenance) and the heatmap PNG. Aggregate: `replay_summary.json` with per-order rows and dataset-level mean/median of `mean_dev`, `p95`, `hausdorff`, plus a **`diverges_from_norm`** boolean per order (flag when an order's `p95` or `hausdorff` exceeds `median + k·MAD` across the batch, `k` configurable, default ~3). This flag is the provenance/traceability surface.

11. **Synthetic-pair generator (zero real data).** Add a `--synthetic N` CLI mode: for each of N orders, generate a fake scan via `synthetic_foot.py`, run it through `generate_orthotic` to get a "produced" part, then create the **reference** as a *slightly perturbed* copy of that produced STL (small rigid offset + mild vertex noise / a scale nudge) so deviations are nonzero but bounded. Write each into an order directory the ingestion path in step 1 accepts, then run the normal replay over them. This must run with no `ANTHROPIC_API_KEY` (Rx supplied as `rx.json`, not parsed).

12. **Review-UI / report surface.** Add one additive read endpoint to `app.py`, e.g. `@app.get("/api/replay/{order_id}")` returning the scorecard JSON + heatmap URL (follow the existing `FileResponse` pattern used by `/api/stl/{result_id}`; do not touch `approve`, `generate`, or their response shapes). In `static/index.html`, add a panel that shows the deviation heatmap and a red **"diverges from historical norm"** badge when `diverges_from_norm` is true. Keep it behind the existing static mount.

**Backward-compat / guardrails**
- Do **not** change `generate_orthotic`'s 6-tuple return `(solid, aligned, hf, top, mask, report)` or its signature — consume it as-is.
- Do **not** alter `validate`'s report keys (`pass`, `checks`, `volume_cm3`, `extents_mm`, `est_print_mass_g_tpu`, plus the `generate_orthotic` `.update()` keys); only *read* `report` into the scorecard.
- `run_demo.py` must still run unchanged (`python run_demo.py`, ~12 s). `replay.py` is additive; no imports of it from the existing pipeline.
- `app.py`: existing endpoints, in-memory `SCANS`/`RESULTS`, and the inline audit write stay exactly as they are; only add the new read endpoint + static panel.
- Keep deps light: `trimesh`/`numpy`/`scipy` are already in `requirements.txt`, and `rtree`/`manifold3d` (signed-distance acceleration) are already hard deps too, so the signed/watertight path works without adding anything. Keep the unsigned `closest_point` fallback as defensive dead-safety and flag it when it fires. No new hard dependency.
- De-id is mandatory: never write patient-identifying keys into any scorecard/summary/audit output.

**Acceptance criteria**
- `python replay.py --synthetic 5 --out /tmp/replay_out` runs **with no `ANTHROPIC_API_KEY` set** and exits 0, producing 5 `*_scorecard.json`, 5 heatmap PNGs, and one `replay_summary.json`. Verify: `test -f /tmp/replay_out/replay_summary.json && ls /tmp/replay_out/*_scorecard.json | wc -l` → `5`.
- Each scorecard JSON contains numeric `mean_dev`, `p95`, `hausdorff` (mm), `vol_delta_cm3`, a `cross_sections` object (arch height, heel-cup depth, rearfoot/forefoot posting deltas), a `landmark_deltas` object with all five landmark names, `rmse`, and a boolean `diverges_from_norm`. For synthetic pairs the deviations are small and bounded (e.g. `p95 < ~2 mm` given a mild perturbation — tune the perturbation so this holds).
- Feeding **identical** meshes (reference == produced) yields `mean_dev ≈ 0`, `hausdorff ≈ 0`, `vol_delta_cm3 ≈ 0` — a sanity assertion the CLI can self-check with a `--selftest` flag.
- De-id: dropping a `patient`/`mrn` key into an order's `rx.json` results in `pii_scrubbed` listing those keys and their absence from every output file.
- `python run_demo.py` still completes and writes its STLs/preview PNGs unchanged.
- With `app.py` running (`python app.py`), `GET /api/replay/<order_id>` returns the scorecard JSON and the heatmap renders in `static/index.html` with the divergence badge appearing for a deliberately over-perturbed synthetic order.

**Effort: L** — one new module plus a synthetic-data harness, ICP reuse, multi-metric geometry (sections + Hausdorff + heatmap render), and a small additive UI/endpoint; each piece is straightforward against existing deps but there are many pieces and a render/optional-dep fallback path to get right.

---

# 2. Landmark accuracy: measurement, calibration, optional learned detector

**Objective.** Add a way to measure how far the current `Heightfield.landmark()` proportional guesses fall from ground-truth landmark positions (in mm), calibrate those ratios/offsets against real data, and (optionally, off by default) scaffold a small learned detector — without touching `generate_orthotic`'s default landmark path unless a regression proves synthetic mods still land correctly.

**Why it matters.** Every mod in `apply_mods` (heel_lift, medial/lateral_wedge, met_pad, relief) is positioned by row/col from `landmark()`; wrong landmarks silently produce a wrong part.

**Files to touch**
- `landmarks.py` — **NEW**: annotation format + loader, measurement pass, calibration fit, and (isolated, optional) learned-detector scaffold.
- `annotations/` — **NEW** dir: one JSON per annotated aligned scan (ground-truth landmark coords). Add to `.gitignore` if scans carry PHI; confirm in code.
- `geometry.py` — **edited**: step 4 extracts the hardcoded fractions inside `landmark()` into a `LANDMARK_PARAMS` data structure that `landmark()` reads. This is an in-place refactor of `landmark()`, but it must be **byte-identical by default** — the extracted defaults equal the current constants, so behavior is unchanged until someone opts into a calibration file (the guardrail + regression below enforce this). Only the default-values-preserved extraction is in scope; `Heightfield`, `align_canonical`, `clean_scan` are otherwise read-only reference.
- `run_demo.py` — read-only reference (regression harness); do not change its output contract.
- `requirements.txt` — no new required deps; learned-detector extra is optional/commented (see guardrails).

**Exact approach**

1. **Annotation format + loader.** One JSON per aligned scan. Landmark names MUST be exactly the five from the `Landmark` alias / `Heightfield.landmark` accepted set: `heel_center`, `base_5th_met`, `met_heads`, `arch_apex`, `hallux`. Store ground truth in **aligned-frame mm** (post-`align_canonical`, so heel y=0, plantar z=0, x=median), because `landmark()` operates on the aligned `Heightfield`:
   ```json
   {"scan": "uploads/97ffe523_sample_scan_right.obj", "side": "right",
    "landmarks": {"heel_center": [x_mm, y_mm], "met_heads": [x_mm, y_mm], ...}}
   ```
   Loader `load_annotation(path) -> dict`. Only the `(x_mm, y_mm)` plantar-plane position is needed for error (landmark returns `(row, col)` → grid indices; z not used).

2. **Grid↔mm conversion (needed to compare).** `Heightfield.landmark(name)` returns `(row, col)` = `(j, i)` ints into the `(ny, nx)` grid with `cell = CELL_MM = 2.0`. Convert a grid index to aligned mm using the field's origin. `y_mm = y_heel*... ` — the exact grid-origin mapping (whether row 0 corresponds to y=0 or to `y.min` bin) is **confirm in code** by reading `Heightfield.__init__` (L252) for how `y_heel`/`y_toe`/`cell` map rows to mm; implement one helper `grid_to_mm(hf, row, col) -> (x_mm, y_mm)` and unit-check it against `heel_center` (should land near y≈`row_at(0.10)*cell`). Do NOT invent an `xn()` helper — it is ABSENT.

3. **Measurement pass.** `measure(annotations, use_icp=True) -> dict`. For each annotated scan: `trimesh.load` → `clean_scan` → `align_canonical(scan, side)` → `Heightfield(aligned)` (mirror the exact `generate_orthotic` preprocessing so measured error reflects production). For each landmark: `grid_to_mm(hf, *hf.landmark(name))` vs ground truth; per-landmark euclidean error in mm. Report per-landmark mean / p95 / max across the set. This step needs **no `ANTHROPIC_API_KEY`** (pure geometry).

4. **Calibrate the heuristics.** The heuristics are the hardcoded fractions inside `landmark()`: `row_at(f)` fractions (0.10/0.63/0.72/0.45/0.90) and the col offsets (`cols.min()+1`, `cols.max()-len(cols)//4`, etc.). Refactor these constants out of `landmark()` into a single data structure (e.g. `LANDMARK_PARAMS: dict[str, dict]`) that `landmark()` reads, so calibration can fit them. **This edits `landmark()` in place, but the extracted defaults MUST equal the current constants so the default path is byte-identical** (see guardrails + the regression that enforces it). Fit each landmark's row-fraction (and col rule where parameterizable) by least-squares / scalar minimization against the mm ground truth — 1-D per parameter, no heavy solver (stdlib/`numpy`/`scipy.optimize.minimize_scalar` — `scipy` is already a dep). Write fitted params to `landmark_calibration.json`. Re-run `measure()` with calibrated params; report before/after per-landmark error delta.

5. **OPTIONAL learned detector (off by default, isolated).** In a clearly separated block/class `LearnedLandmarkDetector` in `landmarks.py`: features from the aligned `Heightfield.Z`/`mask` arrays → landmark coords. Guard **all** ML imports behind a lazy import inside the method so importing `landmarks.py` never pulls ML deps; default construction/measurement path must not import them. Gate activation behind an explicit flag (e.g. `detector="heuristic"` vs `"learned"`), default `"heuristic"`. Any ML dep (scikit-learn or similar) goes in an optional/commented `requirements.txt` line, never a hard import.

6. **Feed error into the replay scorecard.** The replay/scorecard module is ABSENT today (see inventory — no `replay`/`eval` modules); spec `measure()` to return a plain dict of per-landmark mm errors so the (separately-spec'd) scorecard can `.update()` it in, mirroring how `validate`'s report is `.update()`-ed in `generate_orthotic`. Do not create the scorecard here; just expose the dict. (Note: item 1 lands `replay.py` — if it is already present when you reach this item, confirm the dict shape it expects and match it; otherwise implement to the shape above and note the assumption.)

**Backward-compat / guardrails**
- `generate_orthotic`'s return tuple stays exactly `(solid, aligned, hf, top, mask, report)` (6-tuple) — do not add or reorder.
- `validate`'s report keys stay unchanged (`pass`, `checks`, `volume_cm3`, `extents_mm`, `est_print_mass_g_tpu`); landmark error is NOT injected into `validate`'s dict.
- **Do NOT change the default landmark path.** Step 4's refactor edits `landmark()`, but calibrated values must default to the current constants (0.10/0.63/0.72/0.45/0.90 and current col rules) so behavior is byte-identical until someone opts into a calibration file. Swapping in fitted params as the default requires a passing regression proving mods still land on synthetic data (below).
- Keep deps light: `numpy`/`scipy`/`trimesh` only for core; ML deps optional and lazy. No new required dependency.
- `run_demo.py` must still run end-to-end (~12s) and produce STLs/PNGs unchanged.

**Acceptance criteria**
- `python run_demo.py` still completes and emits the same STL/PNG outputs (no ANTHROPIC_API_KEY needed). Confirm the 6-tuple and `report` keys are unchanged.
- `python -c "import landmarks"` succeeds with **no** ML packages installed (proves lazy-import isolation).
- A measurement command — e.g. `python -m landmarks measure annotations/` — prints a per-landmark mm error table (mean/p95/max) for the annotated set; runs with **no ANTHROPIC_API_KEY**. With a synthetic annotation generated from `synthetic_foot.py` (ground truth = the same proportional positions), measured error is ≈0 mm per landmark (sanity that grid↔mm conversion in step 2 is correct).
- A calibrate command — e.g. `python -m landmarks calibrate annotations/` — writes `landmark_calibration.json` and prints before/after per-landmark error; after ≤ before for every landmark on the fit set.
- Regression: with default (uncalibrated) params, a synthetic-scan run through `generate_orthotic` produces the same `top`/`mask`/mods as before the refactor (assert `applied` list and a checksum/max-diff of `top` unchanged) — proving step 4's extraction of constants is behavior-preserving.
- Learned detector remains inert: default measurement/calibration never instantiates `LearnedLandmarkDetector`; enabling it is an explicit flag.

**Effort: M** — measurement + calibration are small numpy/scipy over existing `Heightfield.landmark`; the real cost is the grid↔mm origin mapping (confirm in code) and building a trustworthy synthetic-regression harness; the learned detector is an isolated optional stub.

---

# 3. Rx parser evaluation harness

**Objective.** Add an offline-capable evaluation module that runs `parse_prescription` (or cached fixtures) over a labeled set of `(rx_text, expected Prescription)` cases and scores field-level accuracy: per-mod-type precision/recall, params within tolerance, side assignment, unit conversion, and `needs_manual` firing — emitting a summary plus a confusion list of failures.

**Why it matters.** The parser is currently validated against a single synthetic example; this is the only way to attach a number to accuracy, surface failure modes, and calibrate when `needs_manual` should fire.

**Files to touch**
- **NEW** `eval/__init__.py`
- **NEW** `eval/rx_dataset.py` — seed cases (typed, hand-written now; structured to ingest real de-identified Rx later)
- **NEW** `eval/score_rx.py` — scoring harness + CLI entry (`python -m eval.score_rx`)
- **NEW** `eval/fixtures/<case_id>.json` — cached `Prescription` JSON per case, captured from **real model output** (a live keyed run), for `--offline` (no `ANTHROPIC_API_KEY`)
- **NEW** `eval/README.md` — how to run, how to add real cases, last measured accuracy + `needs_manual` rate
- Do **not** modify `rx_parser.py`, `schema.py`, or `geometry.py`. (`parse_prescription(rx_text, order_id="ORDER") -> Prescription`, `MODEL`, and `SYSTEM_PROMPT` are consumed as-is.)

**Exact approach**

1. **Dataset shape.** In `eval/rx_dataset.py`, each case is `{case_id: str, rx_text: str, expected: Prescription, note: str}`. Build `expected` with real schema objects — `Prescription(order_id=..., left=FootRx(mods=[MedialWedge(degrees=5), HeelLift(height_mm=6)], fill=None), right=..., shell=Shell(...), needs_manual=False, ambiguities=[])`. Seed ~12–20 cases covering: single-side vs bilateral, each `Mod` type (`medial_wedge`, `lateral_wedge`, `heel_lift`, `relief`, `met_pad`), a `Relief` with each `Landmark`, a variable-stiffness `fill` case, unit-conversion cases (cm→mm, "3 degrees" vs "3°"), and at least two designed to trip `needs_manual=True` (contradictory / out-of-range / ambiguous). Keep `order_id` uniform (e.g. the case_id) since `parse_prescription` overwrites it authoritatively.

2. **Run modes.** `score_rx.py` takes `--offline` (default in CI) vs `--live`.
   - `--live`: **lazy-import the parser inside this branch only** — `from rx_parser import parse_prescription` at module top would pull `anthropic` (rx_parser.py L16 top-imports it) and break the offline path when the package is absent. Call `parse_prescription(case.rx_text, order_id=case.case_id)`; if `--write-fixtures`, dump `pred.model_dump_json(indent=2)` to `eval/fixtures/<case_id>.json`. Fixtures capture **real model output** from this live keyed run — **never seed them from `expected`** (that would make every case a trivial full pass and measure nothing). Fixtures may also be hand-authored from real model output, but not copied from the labels.
   - `--offline`: import **`schema` alone** (never `rx_parser`); load `eval/fixtures/<case_id>.json` via `Prescription.model_validate_json(path.read_text())`. Skip (don't fail) cases with no fixture, and report the count skipped. This path imports nothing from `anthropic` and needs no key.

3. **Field-level scoring** (per case, aggregated at the end). For each foot slot in `("left", "right")`:
   - **Side assignment:** compare `expected.<side> is None` vs `pred.<side> is None`. Tally a per-case boolean `side_ok`. A foot present-when-should-be-absent (or vice-versa) is a side error.
   - **Mod presence (precision/recall per type):** for a present foot, form multisets of `m.type` for expected vs predicted mods. Per mod type, accumulate TP/FP/FN across all cases → report precision/recall/F1 per type and micro-averaged.
   - **Param tolerance:** greedily match expected↔predicted mods of the same `type` (one-to-one). For each matched pair compare the type's numeric fields within tolerance and count `param_ok` / `param_total`. Fields by type (from schema): `MedialWedge`/`LateralWedge`→`degrees`; `HeelLift`→`height_mm`; `MetPad`→`height_mm`; `Relief`→`depth_mm`, `radius_mm`, plus exact-match on `landmark`. Default tolerances (tunable module constants): `DEG_TOL = 0.5`, `MM_TOL = 1.0`. Unit-conversion correctness is captured here (a cm-vs-mm miss shows up as a param-out-of-tolerance failure); tag those cases in the confusion list.
   - **`needs_manual` firing:** compare `expected.needs_manual` vs `pred.needs_manual`; accumulate a 2×2 (report `needs_manual` precision/recall/rate).
   - **Fill (light check):** when `expected.<side>.fill` is set, confirm `pred` fill is non-`None` and compare `family`/`density` per zone (`heel/midfoot/forefoot/toe`) within `DENSITY_TOL = 0.1`. Keep this a secondary metric; do not block on it.

   ```python
   # sketch — greedy same-type match, then per-field tolerance
   def match_mods(exp_mods, pred_mods):
       pred = list(pred_mods)
       for e in exp_mods:
           j = next((k for k,p in enumerate(pred) if p.type == e.type), None)
           yield e, (pred.pop(j) if j is not None else None)   # None => FN
       for p in pred:
           yield None, p                                        # leftover => FP
   ```

4. **Report.** Print a summary block (overall case pass-rate; per-mod-type P/R/F1; param-tolerance pass fraction; side-assignment accuracy; `needs_manual` P/R and fired-rate) and a **confusion list**: one line per failing case with `case_id`, the failing dimension(s), and expected-vs-got for the offending field. Exit non-zero if any hard dimension (side, mod-presence, `needs_manual`) regresses below a configurable `--min-*` threshold (default: informational, exit 0) so it can gate CI later.

5. **Tuning loop.** Use the confusion list to iterate on `SYSTEM_PROMPT` / add few-shots in `rx_parser.py` (that edit is a separate change, out of this module's scope), then re-run `--live --write-fixtures` to refresh fixtures and record the new numbers in `eval/README.md`.

**Backward-compat / guardrails**
- No change to `generate_orthotic`'s 6-tuple return `(solid, aligned, hf, top, mask, report)`, to `validate`'s report keys, or to any `schema.py` model — the harness only *reads* `Prescription`/`FootRx`/`Mod` fields.
- Do not alter `rx_parser.parse_prescription` behavior or signature here; the harness treats it as a black box.
- `run_demo.py` must still run unchanged (`python run_demo.py`, ~12s) — the new `eval/` package is not imported by the geometry pipeline or `app.py`.
- New deps: none required. Scoring is stdlib + Pydantic (already a dep). Do **not** pull in `pytest`/`numpy` for this; if a `pytest` wrapper is desired, make it optional and keep the plain `python -m eval.score_rx` path working. `--live` is the only path that imports `anthropic` (already a dep), and it must do so via the in-branch lazy import above; `--offline` must import zero new packages and must not import `rx_parser`.

**Acceptance criteria**
- **Offline, no key** (CI path): `unset ANTHROPIC_API_KEY && python -m eval.score_rx --offline` runs to completion using `eval/fixtures/`, prints the summary + confusion list, and exits 0. Confirm it imports nothing from `anthropic` (e.g. runs with the package absent) — because `--offline` never imports `rx_parser`.
- **Fixtures present:** every seed case in `eval/rx_dataset.py` has a matching `eval/fixtures/<case_id>.json` that `Prescription.model_validate_json` loads without error. Fixtures are generated as a **live/keyed one-shot** (`--live --write-fixtures`) or hand-authored from real model output, then committed — they are **never seeded from `expected`** (a fixture copied from the label would trivially full-pass and measure nothing).
- **Scoring correctness:** a deliberately-wrong fixture (e.g. swap a `HeelLift` height_mm beyond `MM_TOL`, or set `needs_manual` opposite) appears in the confusion list with the correct dimension flagged; a fixture equal to `expected` scores as a full pass. Include a tiny self-check (assert on a synthetic in-memory exp/pred pair — no fixture files, no model call) so scoring logic is CI-testable on its own; this synthetic self-check is the real offline-testable piece.
- **Live path (needs key, not run in CI):** `python -m eval.score_rx --live` calls `parse_prescription`, produces the same report shape, and `--write-fixtures` refreshes the JSON.
- **README numbers:** `eval/README.md` records the measured per-mod-type accuracy and `needs_manual` fired-rate from the latest `--live` run.

**Effort: M** — new self-contained package (dataset + greedy matcher + report + fixtures) with no core-code changes and no new dependencies; bulk of the work is authoring a representative seed set and the per-type scoring bookkeeping.

---

# 4. Pressure-based validation check

**Objective.** Add a deterministic plantar-pressure *estimate* (Winkler elastic-foundation spring-bed, not FEA) to the validation step in `geometry.py`, producing a per-cell pressure map (kPa) plus peak/mean/location metrics that feed the `report` dict and a heatmap preview. The local spring stiffness tracks the zone fill density, so softer lattice zones show measurably lower peak pressure.

**Why it matters.** A geometric "pass" is not clinical proof — a cheap pressure proxy makes validation meaningful and gives the review UI an offloading story competitors (Phits) don't surface.

**Files to touch.**
- `/Users/sam/Documents/Claude/Orthopipe/geometry.py` — new constants, new `estimate_pressure(...)` helper, wire into `generate_orthotic` (metrics merged onto `report` — **not** by changing `validate`'s signature).
- `/Users/sam/Documents/Claude/Orthopipe/run_demo.py` — write the pressure heatmap PNG next to existing previews (confirm in code how previews are currently rendered).
- No schema change: reuse the existing `Fill` / `ZoneFill` (`family`, `density` 0.0–1.0) and `FootRx.fill: Optional[Fill]` from `schema.py`.

**Prerequisite assumption.** Batch-1's lattice generator is described as landed, but the current `geometry.py` inventory shows lattice **ABSENT** (no `lattice.py`, not wired into `generate_orthotic`). Confirm this in code first. This item does **not** depend on a solid lattice mesh existing — it only needs a per-cell *relative-density field* ρ∈(0,1]. Derive ρ directly from `rx.fill` zone densities mapped onto the `Heightfield` grid (below). If `rx.fill is None`, ρ≡1 (solid) and the check still runs against a solid baseline.

**Exact approach.**

1. **Constants** (near `CELL_MM`/`MIN_THICKNESS_MM`, all tunable):
   ```python
   BODY_LOAD_N        = 350.0    # ~50% BW of a 70 kg subject, single-foot stance
   E_SOLID_PA         = 20.0e6   # nominal TPU-ish compressive modulus (tune vs bench)
   GIBSON_ASHBY_N     = 2.0      # bending-dominated lattice exponent: E_eff ≈ E_solid*ρ^n
   PRESSURE_PEAK_MAX_KPA = 600.0 # above this = implausible (clinical peaks ~200–500 kPa)
   ```

2. **Density field** `_density_field(hf, mask, rx) -> np.ndarray` (shape `(hf.ny, hf.nx)`):
   - start `rho = np.ones((hf.ny, hf.nx))`.
   - if `rx.fill` is set, map each `ZoneFill.density` onto rows by normalized length using `hf.yn()` (per-row normalized heel→toe length; confirm returned shape in code). Suggested yn bands (tunable, align with `apply_mods` conventions): `heel [0,0.25)`, `midfoot [0.25,0.55)`, `forefoot [0.55,0.85)`, `toe [0.85,1.0]`. Clamp `rho` to a small floor (e.g. `max(density, 0.05)`) to avoid zero stiffness.

3. **Pressure solve** `estimate_pressure(top, mask, hf, rx) -> tuple[np.ndarray, dict]` — receives the final modded `top` and `mask` (the same arrays `generate_orthotic` already holds at L443):
   - per-cell tributary area `A = (hf.cell/1000.0)**2` m² (`CELL_MM=2.0`).
   - local thickness `t_i = max(top_i, MIN_THICKNESS_MM)/1000.0` m; `E_eff_i = E_SOLID_PA * rho_i**GIBSON_ASHBY_N`; foundation modulus `k_i = E_eff_i / t_i` (Pa/m). Softer/thicker → lower `k_i`.
   - rigid-flat-indenter closure: `gap_i = top.max() - top_i` (m, over `mask`); penetration `δ_i(Δ) = max(Δ - gap_i, 0)`; solve scalar `Δ` s.t. `Σ_i k_i·A_i·δ_i(Δ) = BODY_LOAD_N` by 1-D bisection (total force is monotonic increasing in Δ). ~40 iterations is plenty and deterministic.
   - `p_i = k_i * δ_i(Δ)` Pa → `p_kpa = p_i/1000` (0 off-mask).
   - **solid baseline:** re-run the same solve with `rho≡1` to get `peak_solid_kpa` (this is the offloading reference).

4. **Metrics dict** (all NEW, additive):
   ```python
   {"peak_pressure_kpa": round(float(p_kpa.max()), 1),
    "mean_pressure_kpa": round(float(p_kpa[mask].mean()), 1),
    "peak_location": [int(r), int(c)],           # argmax row,col in hf grid
    "contact_area_cm2": round(mask.sum()*(hf.cell/10.0)**2, 1),
    "peak_pressure_solid_kpa": round(float(peak_solid_kpa), 1)}
   ```

5. **Wire in via `report.update()` — do NOT change `validate`'s signature.** `validate(mesh, hf)` currently ignores `hf`; leave its signature and its single call site untouched. `generate_orthotic` already holds `top`, `mask`, `hf`, and the foot's `rx` at that point, so compute pressure there: `p_kpa, metrics = estimate_pressure(top, mask, hf, rx)`, then `.update()` the `metrics` onto `report` alongside the existing L441-442 updates. Add two boolean checks into `report["checks"]` (merge them in after `validate` returns) and, if `report["pass"]` is derived from `checks`, recompute it as `report["pass"] = all(report["checks"].values())` so the new checks participate:
   - `"peak_pressure_plausible": metrics["peak_pressure_kpa"] <= PRESSURE_PEAK_MAX_KPA`
   - `"pressure_reduced_vs_solid": metrics["peak_pressure_kpa"] <= metrics["peak_pressure_solid_kpa"] + 1e-6` (true and trivially satisfied when `rx.fill is None`, since map == baseline).
   Keep thresholds lenient enough that the `run_demo.py` scan still yields `report["pass"] == True`.

6. **Heatmap preview.** In `run_demo.py`, render `p_kpa` (masked) as a PNG using whatever the existing preview step uses (confirm in code — the preview path is **matplotlib Agg**, so `plt.imshow(np.where(mask, p_kpa, np.nan), cmap="inferno")` with a kPa colorbar, then `fig.savefig(...)`). Do not add a new hard dependency solely for this — reuse the existing matplotlib preview toolchain.

7. **Future accuracy (do NOT implement now):** note in a comment that CalculiX (`ccx`, `*HYPERFOAM` contact) or FEniCSx is the heavier real-FEA upgrade path from this spring-bed proxy.

**Backward-compat / guardrails.**
- `generate_orthotic`'s 6-tuple return `(solid, aligned, hf, top, mask, report)` must stay exactly as-is.
- All existing `report` keys (`pass`, `checks`, `volume_cm3`, `extents_mm`, `est_print_mass_g_tpu`, plus the L441-442 `side`/`mods_applied`/`warnings`/`foot_length_mm`/`output`) and existing `checks` sub-keys must remain — pressure keys/checks are **additive only**.
- `validate`'s signature `validate(mesh, hf)` is **unchanged** — pressure metrics are merged onto `report` in `generate_orthotic`, not passed through `validate`.
- `report["pass"]` stays a boolean; tune thresholds so the default `run_demo.py` output still passes.
- No new required dependency: numpy/scipy/trimesh are already imported; the solve is pure numpy. Any plotting reuses the current preview path.
- Determinism: fixed iteration count, no RNG — same scan → same pressure numbers.

**Acceptance criteria** (no `ANTHROPIC_API_KEY` needed — geometry path only):
1. `source .venv/bin/activate && python run_demo.py` completes in ~12s, prints a `report` containing `peak_pressure_kpa`, `mean_pressure_kpa`, `peak_location`, `contact_area_cm2`, `peak_pressure_solid_kpa`, and `report["pass"] is True`; the peak ideally lands in a clinically plausible band (roughly 100–500 kPa). **This band is advisory, not a hard gate** — `E_SOLID_PA`/`BODY_LOAD_N` are first-pass guesses and the band may not hold on the first run; the only hard gate is the `peak_pressure_plausible` check (`<= PRESSURE_PEAK_MAX_KPA = 600`) that keeps `report["pass"]` True. If the peak falls outside 100–500, flag it and tune the constants rather than failing the item. A pressure-heatmap PNG is written next to the existing preview PNGs.
2. Lattice-coupling smoke test (pure Python, no API key), e.g.:
   ```python
   from schema import FootRx, Fill, ZoneFill
   # solid vs softer heel (density 0.3)
   rx_soft = FootRx(fill=Fill(heel=ZoneFill(family="gyroid", density=0.3)))
   ```
   Run `estimate_pressure` on the same `top/mask/hf` with `rx.fill=None` vs `rx_soft`; assert the softer-heel peak `<` solid-baseline peak (offloading works and the map responds to the fill field).
3. Existing keys regression: assert `set(prior_report_keys).issubset(new_report.keys())` and every original `checks` sub-key still present.

**Effort: M** — one self-contained numpy solver plus a small heightfield→density mapping and a preview hook; the only ambiguity is reusing `run_demo.py`'s existing matplotlib preview rendering and confirming `hf.yn()`'s shape.

---

# 5. Local-LLM fallback for the Rx parser (PHI de-risk)

**Objective.** Refactor `rx_parser.parse_prescription` into a pluggable-backend function so the same validated `Prescription` can be produced either by the current Anthropic `messages.parse` path (default) or by a fully local model (Ollama or llama.cpp) selected via an env var, keeping the Pydantic schema as the single contract.

**Why it matters.** Prescription free-text can carry PHI; a local backend removes the one hard cloud dependency and lets a clinic run without a BAA.

**Files to touch.**
- `/Users/sam/Documents/Claude/Orthopipe/rx_parser.py` — extract the backend seam; add dispatcher.
- `/Users/sam/Documents/Claude/Orthopipe/rx_backends.py` — **NEW**: backend implementations (Anthropic default + local).
- `/Users/sam/Documents/Claude/Orthopipe/docs/rx-backends.md` — **NEW**: backend selection + Anthropic BAA path.
- `/Users/sam/Documents/Claude/Orthopipe/requirements.txt` — do **not** add `ollama`/`llama-cpp-python` as hard deps; note them as optional extras only.
- Rx eval harness from item 3 — **confirm in code** (inventory shows `eval` modules ABSENT; assume item 3 lands an eval entrypoint that already imports `parse_prescription`. If absent, the spec below still stands — the new `backend=` kwarg is additive and the harness parametrizes over it).

**Exact approach.**

1. **Define the seam.** In `rx_backends.py`, extract everything currently between client construction and the `order_id` override (rx_parser.py L58–71) behind a uniform signature. Keep `MODEL` (L20, `"claude-opus-4-8"`) and `SYSTEM_PROMPT` (L22–49) in `rx_parser.py` and pass them in:
   ```python
   # rx_backends.py
   def anthropic_backend(system: str, user: str, output_format) -> Prescription: ...
   def local_backend(system: str, user: str, output_format) -> Prescription: ...
   ```
   Each returns a validated `Prescription`. The `rx.order_id = order_id` override (L72) stays in `parse_prescription`, outside the backend.

2. **Anthropic backend = current behavior verbatim.** Move the L58–68 block in: `anthropic.Anthropic()`, `client.messages.parse(model=MODEL, max_tokens=16000, system=system, messages=[{"role":"user","content":user}], output_format=output_format)`, the `stop_reason == "refusal"` → `RuntimeError` check, and `response.parsed_output`. This path is byte-for-byte what runs today.

3. **Local backend = JSON-schema-constrained decode, then manual validate.** A local model has no `messages.parse` equivalent, so:
   - Derive schema from Pydantic: `output_format.model_json_schema()`.
   - **Ollama** (default local impl): `from ollama import chat`; `resp = chat(model=os.environ.get("ORTHOPIPE_RX_LOCAL_MODEL", "llama3.1"), messages=[{"role":"system","content":system},{"role":"user","content":user}], format=output_format.model_json_schema(), options={"temperature": 0})`; then `output_format.model_validate_json(resp.message.content)`.
   - **llama.cpp** alternative (confirm which to ship; Ollama is the lighter default): `LlamaGrammar.from_json_schema(...)` passed as `grammar=` to `create_chat_completion`, or llama-server `response_format={"type":"json_schema", ...}`. Because the schema constrains tokens but is not shown to the model, keep `SYSTEM_PROMPT` (which already describes structure, mods, fill families/zones) as the prompt — it stays provider-agnostic.
   - Import the local client **lazily inside the function** so importing `rx_parser` never requires `ollama`/`llama-cpp-python`.

4. **Dispatcher + env var.** Add an optional `backend: str | None = None` kwarg to `parse_prescription(rx_text, order_id="ORDER", backend=None)` so the eval harness can force a path; when `None`, read the env var. Then select before calling, **raising `ValueError` on an unknown name** (bare dict indexing would raise `KeyError`, but acceptance requires `ValueError`):
   ```python
   _BACKENDS = {"anthropic": anthropic_backend, "local": local_backend}
   name = backend or os.environ.get("ORTHOPIPE_RX_BACKEND", "anthropic")
   if name not in _BACKENDS:
       raise ValueError(f"unknown ORTHOPIPE_RX_BACKEND={name!r}; expected one of {sorted(_BACKENDS)}")
   backend_fn = _BACKENDS[name]
   ```
   Default resolves to `"anthropic"` — unchanged behavior.

5. **Fail loud, never silent-fallback.** If the selected backend name is unknown → `ValueError` (per step 4). If `"local"` is selected but the client import fails or the server isn't reachable → raise `RuntimeError` with a clear message (`"ORTHOPIPE_RX_BACKEND=local requires the 'ollama' package and a running Ollama server"`). Do **not** fall back to Anthropic.

6. **Docs.** In `docs/rx-backends.md`: (a) env-var selection table; (b) the BAA fact — for PHI on the cloud path you must be on the Messages API under a signed Anthropic BAA (Console/Workbench and Pro/Team are not covered; Covered Models require 30-day retention, so ZDR is not available for them); (c) the local path as the PHI-safe default with no data leaving the machine.

7. **Eval harness extension.** Parametrize the item-3 Rx eval over `backend in ("anthropic","local")` via the new kwarg (**confirm harness entrypoint in code**). Skip the `anthropic` case when `ANTHROPIC_API_KEY` is unset; skip the `local` case when Ollama is unreachable — so CI can run whichever is available.

**Backward-compat / guardrails.**
- `parse_prescription(rx_text, order_id="ORDER")` signature stays call-compatible (new kwarg is optional, defaults preserve today's behavior); `app.py` `/api/parse_rx` (`parse_prescription(...)` → `rx.model_dump()`) is untouched.
- Default env (unset `ORTHOPIPE_RX_BACKEND`) must reproduce the exact current Anthropic path, including the `order_id` override and refusal check.
- `geometry.py` is not touched: `generate_orthotic`'s 6-tuple return `(solid, aligned, hf, top, mask, report)` and `validate`'s report keys (`pass`, `checks`, `volume_cm3`, `extents_mm`, `est_print_mass_g_tpu`) are out of scope and must not change.
- `run_demo.py` (geometry-only, no parser) must still pass unchanged.
- No new **hard** deps: `ollama`/`llama-cpp-python` stay optional/lazy-imported; `requirements.txt` core install must still succeed without them.

**Acceptance criteria.**
- `ORTHOPIPE_RX_BACKEND` unset, with `ANTHROPIC_API_KEY` set: `python -c "from rx_parser import parse_prescription; print(parse_prescription('4mm medial wedge left', 'ORD1').order_id)"` prints `ORD1` (unchanged path).
- **No API key needed:** unknown backend fails fast — `ORTHOPIPE_RX_BACKEND=bogus python -c "from rx_parser import parse_prescription; parse_prescription('x')"` raises `ValueError` (not a network call, not a `KeyError`).
- **No API key needed:** `python -c "import rx_parser"` succeeds with `ollama`/`llama-cpp-python` **not** installed (proves lazy import / light default).
- **No API key needed:** `ORTHOPIPE_RX_BACKEND=local` with no Ollama running raises a clear `RuntimeError` mentioning `local` and the missing dependency/server — and never contacts Anthropic (confirm no `anthropic.Anthropic()` construction on this path).
- With Ollama running locally: `ORTHOPIPE_RX_BACKEND=local python -c "from rx_parser import parse_prescription; p=parse_prescription('softer heel, 4mm medial wedge, left foot','ORD2'); print(p.order_id, bool(p.left))"` returns a validated `Prescription` (`p.order_id == "ORD2"`) with no network egress.
- `python run_demo.py` still emits STLs + preview PNGs (~12s), unaffected.
- Eval harness runs green over both backends, auto-skipping whichever is unavailable.

**Effort: M** — small, well-isolated refactor (one seam + dispatcher + docs), but a second inference path and its eval coverage add real surface to get right.

---

# 6. Phone-scan-only intake path

**Objective.** Let a clinician upload a phone-captured mesh (TrueDepth/LiDAR export) through the *existing* `/api/upload` → `/api/generate` flow, adding a format-normalization helper (usdz/ply → obj) and a units/scale sanity check (meters-vs-mm), plus a neutral-position capture prompt in the review UI.

**Why it matters.** Removes the scanner-hardware lock-in and lowers the barrier for the small clinics OrthoPipe wins first — a phone + a neutral-position guide is the entire capture rig.

**Product/GTM item — independent of all calibration/engine work. It rides entirely on the current `.obj/.stl/.ply` upload path and needs no changes to `generate_orthotic`.**

**Files to touch**
- `app.py` — extend `/api/upload` accepted extensions; call normalization + scale-check before storing in `SCANS`.
- `intake.py` — **NEW** — normalization + units helpers (keep it a small standalone module; no geometry-engine coupling).
- `static/index.html` **and** root `index.html` — add the capture-guidance prompt near the upload control (CLAUDE.md says keep both in sync).
- `requirements.txt` — only if the optional usd-core path is adopted (see guardrails).

**Exact approach**

1. **Widen accepted formats at upload.** `/api/upload` (app.py L38-47) currently gates on `file.filename.lower().endswith((".obj",".stl",".ply"))`. Add `.usdz` to that tuple and update the 400 message string. `.obj/.stl/.ply` already load natively in `trimesh` per the inventory — no conversion needed for those; only `.usdz` requires tooling.

2. **New `intake.py` — `normalize_scan(path: str) -> tuple[str, list[str]]`.** Returns `(obj_or_native_path, warnings)`. Logic:
   - `.obj/.stl/.ply` → return the path unchanged (trimesh loads these directly; `generate_orthotic` already does `trimesh.load(scan_path, force="mesh")`).
   - `.usdz` → attempt conversion to `.obj` via `usd-core` (`from pxr import Usd, UsdGeom`): open the stage, traverse `UsdGeom.Mesh` prims, read `points` + `faceVertexIndices`, build `trimesh.Trimesh(vertices, faces)`, `.export(<same_stem>.obj)`, return the new path. **Degrade gracefully:** wrap the `pxr` import in try/except; if usd-core is absent, append a warning like `"usdz upload received but USD tooling not installed — export .obj/.ply from the capture app"` and raise a clean `ValueError` the endpoint maps to HTTP 400. Do NOT hard-crash. (Confirm the exact usdz triangulation handling in code — `faceVertexCounts` may include quads/ngons that need fan-triangulation before building the Trimesh.)

3. **New `intake.py` — `check_and_fix_scale(mesh: trimesh.Trimesh) -> tuple[trimesh.Trimesh, list[str]]`.** Phone captures are sometimes in meters. Heuristic on `mesh.extents` (an `(x,y,z)` array; foot length is the long axis ≈ y):
   ```
   long = max(mesh.extents)
   if long < 1.0:          # a real foot is never < 1 unit if mm; ~0.12–0.36 if meters
       mesh.apply_scale(1000.0)
       warnings.append("scan appears to be in meters; auto-scaled ×1000 to mm")
   elif long < 40:         # ambiguous small — warn, don't touch
       warnings.append(f"scan long-axis {long:.1f} — below plausible foot length; check units")
   ```
   Anchor the mm thresholds to `validate`'s own plausibility band (`length_plausible: 120 <= ext[1] <= 360`, geometry.py L408) so a scaled scan lands inside it. Prefer running the scale check on the loaded mesh and re-exporting, OR expose it so it runs pre-`generate_orthotic`; the geometry engine re-floors/re-orients in `align_canonical`, so scaling must happen *before* the heightfield is built. Simplest wiring: do scale-check inside `normalize_scan` after load and re-export the corrected mesh.

4. **Wire into `/api/upload` only.** After saving raw bytes to `UPLOADS / f"{scan_id}_{file.filename}"`, call `normalize_scan(...)`; store the *returned* path (possibly the converted/rescaled `.obj`) plus the merged warnings into the `SCANS` dict alongside the existing `{path, filename, uploaded_at}`. `/api/generate` (L80-110) looks up `scan["path"]` and needs **no change** — it just receives an already-normalized `.obj`. Surface `warnings` in the `/api/upload` JSON response (add a `"warnings"` key alongside `{"scan_id","filename"}`) so the UI can show them.

5. **UI capture guidance.** In both `index.html` files, near the file-input control, add static instructional copy: neutral subtalar position (foot relaxed, ankle neutral, non-weight-bearing per clinic protocol), even lighting, slow orbit, and **"export .obj or .ply from your capture app"** as the recommended formats (usdz supported only if USD tooling is present server-side). No JS logic required beyond optionally rendering the new `warnings` array from the upload response. (Confirm exact upload-control markup/IDs in code.)

**Backward-compat / guardrails**
- **Do NOT touch `geometry.py`.** `generate_orthotic`'s 6-tuple return `(solid, aligned, hf, top, mask, report)` and `validate`'s report keys (`pass`, `checks`, `volume_cm3`, `extents_mm`, `est_print_mass_g_tpu`, plus the `.update()`-ed `side/mods_applied/warnings/foot_length_mm/output`) are unchanged — this feature ends at the upload boundary.
- **`run_demo.py` must still pass** (`.obj` path, no upload involved) — it never calls `intake.py`, so it is unaffected; verify it still runs in ~12s.
- **Existing `.obj/.stl/.ply` uploads must behave identically** — `normalize_scan` returns those paths untouched (aside from the meters heuristic, which only fires on sub-mm-scale extents).
- **New dep is optional.** `usd-core` is heavyweight; make the `pxr` import lazy/optional so the whole app installs and runs without it, and only `.usdz` uploads fail (gracefully, with the warning above). Do not add it to the default `requirements.txt` unless the team commits to server-side usdz — document it as an optional extra. `trimesh`/`numpy` are already deps.
- Keep the endpoint's existing "no size limit / no mesh validation at upload" posture unless explicitly asked — real validation stays in the geometry engine.

**Acceptance criteria**
- **No API key needed for any of this** (parser/`ANTHROPIC_API_KEY` is untouched).
- `python run_demo.py` still completes (~12s) and writes STLs + preview PNGs — proves geometry path unregressed.
- Unit-level, no server: `python -c "import trimesh, intake; m=trimesh.load('uploads/97ffe523_sample_scan_right.obj', force='mesh'); m2,w=intake.check_and_fix_scale(m); print(m2.extents, w)"` → prints extents already in mm (long axis in the ~120–360 band) with an **empty** warnings list (the sample is already mm).
- Meters case: scale the sample by 0.001, run `check_and_fix_scale`, expect a `×1000` warning and extents back in the plausible band.
- `.usdz` without usd-core: `POST /api/upload` a `.usdz` → HTTP 400 with the "export .obj/.ply" guidance, and the server does not crash (other endpoints still respond). Start server per CLAUDE.md: `python app.py`.
- `.obj` upload still returns `{"scan_id","filename", ...}` and a subsequent `/api/generate` produces a valid `report` — confirm the phone-intake plumbing is transparent to the happy path.
- Both `index.html` files show the neutral-position capture prompt (grep both for the guidance string).

**Effort: S** — one small new module (two helpers) + a one-line extension to `/api/upload` + static UI copy; the only real risk is the optional usdz/usd-core branch, which is isolated and degrades to a 400.

---

# Definition of done (global checklist)

- [ ] For **every** item completed: `source .venv/bin/activate && python run_demo.py` runs in ~12s and prints `report["pass"] is True` — no regressions to STL/preview outputs.
- [ ] `generate_orthotic`'s 6-tuple return `(solid, aligned, hf, top, mask, report)` and signature are byte-for-byte unchanged.
- [ ] `validate`'s report keys (`pass`, `checks`, `volume_cm3`, `extents_mm`, `est_print_mass_g_tpu`) and existing `checks` sub-keys are all still present; any new keys/checks are **additive only** (item 4), and `validate`'s own signature is unchanged.
- [ ] No new **hard** dependency: `requirements.txt` core install still succeeds; every added dep (ML libs, `ollama`/`llama-cpp-python`, `usd-core`) is optional, lazy-imported, and degrades gracefully when absent. (`rtree`/`manifold3d` are already hard deps, so item 1's signed-distance path needs nothing new.)
- [ ] Every new module imports cleanly with **no `ANTHROPIC_API_KEY`** and with optional deps absent (`import landmarks`, `import rx_parser`, `import intake`, `python -m eval.score_rx --offline`, `python replay.py --synthetic N`). In particular, `--offline` eval never imports `rx_parser`/`anthropic`.
- [ ] De-identification holds: no patient-identifying keys are ever written to any scorecard/summary/audit/output file (item 1).
- [ ] Batch-1 dependencies were **verified in code** before building on them; where incomplete, the code implements against current reality with an inline note on the assumption made.
- [ ] Each item's own acceptance criteria pass (see per-section lists).
- [ ] `app.py` existing endpoints, in-memory `SCANS`/`RESULTS`, response shapes, and audit write are untouched except for the explicitly-additive read endpoint (item 1) and the widened `/api/upload` extensions + `warnings` key (item 6).
- [ ] Changes are minimal and scoped — no unrelated refactors; code style matches the existing repo.

---

# Not a code task (one remaining research item)

**Regulatory classification — confirm before any market claims.** Determine the exact **FDA product code / device classification** for OrthoPipe's output (custom foot orthoses / the software as a medical device question) with a **qualified regulatory consultant**. This is a regulatory/legal research item, **not** an implementation task — do not attempt to encode a classification, add compliance claims to the UI, or make marketing/regulatory assertions in code or docs until this is confirmed by a professional.
