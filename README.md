# OrthoPipe — Geometry Engine Prototype

Working prototype of the core described in `orthotic-mvp-dev-doc.md` §4.4:
scan `.obj` + parsed prescription JSON → validated, print-ready orthotic STL.

## Run it

```bash
pip install trimesh numpy scipy shapely rtree matplotlib pydantic pymeshlab
python run_demo.py
```

Demo runs the peer's exact example Rx — *"Bilateral Medial Wedges, .25in Heel
lift on Right only. Lateral base of 5th relief, both feet"* — against two
synthetic scans (noisy, misaligned, with floating debris) and produces:

- `DEMO-0001_left.stl`, `DEMO-0001_right.stl` — watertight solids, slicer-ready
- `DEMO-0001_{side}_preview.png` — 4-panel reviewer render (aligned scan,
  top-surface heatmap with landmarks, output solid, sagittal profile)
- JSON validation report per foot (watertight / volume / dimensions / TPU mass)

Full demo (both feet + renders): ~12 s. Geometry alone: ~3 s/foot.

## Files

| File | Role |
|---|---|
| `schema.py` | Pydantic Rx schema — the LLM↔geometry contract |
| `geometry.py` | Cleanup → alignment → heightfield → shell → mods → validate → export |
| `synthetic_foot.py` | Fake Comb-style scan generator (dev only) |
| `run_demo.py` | End-to-end demo + reviewer previews |

## Architecture decisions embodied here

1. **Heightfield, not booleans.** The orthotic is a `Z(x,y)` surface over a
   flat bottom; every mod (wedge, lift, relief, met pad, cup) is array math.
   Watertightness is by construction, and generation is deterministic.
2. **Fixed mod ordering in code** (`apply_mods`) — the LLM chooses *parameters*,
   never sequencing.
3. **Canonical frame first.** All anatomy heuristics (landmarks, medial side,
   heel/toe) run on an aligned right-foot frame; left feet mirror in and out.
4. **Min-thickness clamp** (2 mm) — reliefs can never punch through the shell.
5. **Alignment heuristics emit warnings**, not silent guesses — surfaced to the
   reviewer.

## Known prototype limitations (v1 backlog)

- Landmarks are proportional heuristics; validate against real scans in Phase 0
  and add reviewer drag-to-correct.
- `clean_scan` is minimal; wire in the pymeshlab close-holes/decimate recipe for
  real (hole-riddled) Comb exports.
- Left/right auto-detection not attempted — side comes from the work order.
- Trim outlines derive from the footprint; production wants template outline
  library scaled to foot length.
- Synthetic scans only — first task with real data is replaying historical
  orders through `generate_orthotic` and diffing against fabricator output.

## Next integration steps

1. Swap `synthetic_foot.py` for real de-identified `.obj` exports.
2. Add the Claude Rx-parser call emitting `Prescription` (schema already final).
3. `prusa-slicer --export-gcode --load tpu95a.ini` on validated STLs.
4. FastAPI wrapper + review page per dev doc §4.5.

## v1.1 — Review Station (web UI)

```bash
pip install -r requirements.txt
python app.py
```
Open port 8000 in the browser. Workflow: drag in scan -> set Rx mods ->
Generate -> inspect 3D model + validation -> Approve (audit-logged) -> STL downloads.
