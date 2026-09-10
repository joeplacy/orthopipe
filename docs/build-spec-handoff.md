# OrthoPipe — Implementation Handoff

## You are working on

**OrthoPipe** is a pipeline that turns a 3D foot scan (`.obj`) plus a clinician prescription into a validated, 3D-print-ready orthotic STL. Stack: **Python, trimesh, pymeshlab, pydantic, FastAPI**.

Key files:
- `schema.py` — Pydantic Rx contract (the LLM↔geometry interface).
- `geometry.py` — the pipeline: cleanup → align → heightfield → shell → mods → validate → export.
- `rx_parser.py` — Claude-powered free-text Rx → `Prescription`.
- `app.py` — FastAPI review UI + audit log.

Dev setup:
```bash
source .venv/bin/activate
python run_demo.py   # end-to-end integration check (~12s) — MUST keep passing
python app.py        # serves UI on :8000; also copies index.html -> static/index.html
```

## How to work

- Make the **smallest change** that satisfies each spec. Do not refactor unrelated code.
- **Preserve public signatures and return shapes** unless a spec explicitly says to change them. In particular, `generate_orthotic` must keep its 6-tuple return `(solid, aligned, hf, top, mask, report)`.
- After each item, run `python run_demo.py` and confirm the validation report still passes (`watertight`, `positive_volume`, plausible dims; `report["pass"] == True`) **before moving on**.
- Match existing code style.
- If you introduce a dependency, add it to `requirements.txt` (and confirm it installs on the 3.11 `.venv`).
- **Ask nothing** — implement to the spec. Where the spec says "confirm in code," verify against the actual source and note any assumption inline as a comment.

## Sequencing

Items **1–2** unblock real scan data; items **3–4** build the variable-stiffness lattice (the product differentiator); item **5** hardens the regulatory record. Do them in order — they are mostly independent, with the one hard dependency being that **item 4 consumes item 3's schema**.

---

## 1. Wire PyMeshLab mesh repair into `clean_scan`

**Objective.** Replace the trimesh-only `clean_scan` stub in `geometry.py` with a real PyMeshLab `MeshSet` repair recipe (dedup → close holes → optional quadric decimation → light Taubin smoothing) that runs before `align_canonical`, so hole-riddled real scanner exports become watertight-ish, print-ready input. Keep the signature and return type intact; make heavy repair skippable for the synthetic-foot path; surface every substantive action as a warning rather than a silent guess.

**Why.** Real scanner exports are hole-riddled — this is the #1 blocker to real de-identified scan data. `pymeshlab` is already a declared dependency but unused.

**Files.** `geometry.py` only — rewrite `clean_scan` (currently L29-41) and adjust its call site in `generate_orthotic` (L295, `scan = clean_scan(scan)`). No new files. `requirements.txt` already lists `pymeshlab` — confirm; do not re-add.

**Approach.**
1. **Import.** First verify the wheel is actually installed in the `.venv` (`pip show pymeshlab`) — CLAUDE.md declares it, but the venv may lack the wheel. If it is installed, add `import pymeshlab` at the top alongside `numpy`/`trimesh` (Python 3.10+ is required precisely because of it). **If you cannot confirm it is installed, do NOT add a top-level hard import** — a missing wheel makes `geometry.py` unimportable and takes down both `run_demo.py` **and** `app.py` at import time, a regression from today (geometry.py currently imports cleanly with pymeshlab unused). In that case keep the import **local to `clean_scan`** and raise a clear error if heavy repair is requested without the wheel.
2. **Extend the signature without breaking callers:**
   ```python
   def clean_scan(
       mesh: trimesh.Trimesh,
       heavy_repair: bool = True,
       target_faces: int | None = 80000,
       warnings: list[str] | None = None,
   ) -> trimesh.Trimesh:
   ```
   Return type stays `trimesh.Trimesh`. `heavy_repair=False` is the synthetic-foot escape hatch (skip close-holes + decimation, keep cheap dedup + smoothing). If `warnings is None`, create a local list so behavior is unchanged when omitted.
3. **Keep existing trimesh pre-steps as a fast guard.** Retain the largest-component split (`mesh.split(only_watertight=False)`, keep max `.area` component if >1 part) and `mesh.update_faces(mesh.nondegenerate_faces())` / `remove_unreferenced_vertices()` before handing to PyMeshLab — this drops junk that would skew PCA in `align_canonical`. Warn if >1 component was discarded.
4. **Round-trip trimesh → `MeshSet` → trimesh, in memory (no temp file):**
   ```python
   ms = pymeshlab.MeshSet()
   ms.add_mesh(pymeshlab.Mesh(vertex_matrix=mesh.vertices, face_matrix=mesh.faces))
   ```
   Confirm the constructor kwarg names in code; if the in-memory constructor is problematic, fall back to `ms.load_new_mesh(path)` on a temp `.ply` in the scratchpad + `ms.save_current_mesh(...)`. `ms.current_mesh()` exposes `.vertex_matrix()` / `.face_matrix()`.
5. **Repair recipe (current PyMeshLab filter names — pre-2022.2 names are deprecated):**
   - Dedup/cleanup: `meshing_remove_duplicate_vertices()`, `meshing_remove_duplicate_faces()`, `meshing_remove_unreferenced_vertices()`, `meshing_remove_null_faces()`.
   - Close holes: `ms.meshing_close_holes(maxholesize=40)` (note the chosen value in a comment). Optionally `meshing_repair_non_manifold_edges()` first if hole-filling fails on non-manifold input.
   - **Decimation, gated on `heavy_repair and target_faces` and only if over budget:**
     ```python
     if heavy_repair and target_faces and ms.current_mesh().face_number() > target_faces:
         ms.meshing_decimation_quadric_edge_collapse(
             targetfacenum=target_faces, preservenormal=True, planarquadric=True)
     ```
   - Light Taubin smoothing: `ms.apply_coord_taubin_smoothing(stepsmoothnum=10)` (keep `lambda_`≈0.5, `mu`≈-0.53 comparable to the old `filter_taubin(lamb=0.5, nu=-0.53, iterations=6)`). Note `lambda_` has a trailing underscore.
   - When `heavy_repair=False`: run only dedup + Taubin; skip close-holes and decimation.
6. **Rebuild + preserve invariants.** Return `trimesh.Trimesh(vertices=ms.current_mesh().vertex_matrix(), faces=ms.current_mesh().face_matrix(), process=True)`. Do **not** re-floor, re-scale, or re-orient — orientation, mirroring, and the plantar `z=0` floor belong to `align_canonical` (L57-112). `clean_scan` stays a pure repair pass.
7. **Warnings, not silent guesses.** Replace the current `try/except BaseException: pass`. On each substantive action or failure, append to `warnings`, e.g. `"clean_scan: closed N holes (maxholesize=40)"`, `"clean_scan: decimated 210k->80k faces"`, `"clean_scan: discarded 3 stray components"`, or `"clean_scan: meshing_close_holes failed (<err>); proceeding with unrepaired mesh"`. Use before/after `face_number()` / `vertex_number()` counts.
8. **Thread warnings through `generate_orthotic` (L293-309):**
   ```python
   warnings = []
   scan = clean_scan(scan, warnings=warnings)
   aligned, align_warnings = align_canonical(scan, side)
   warnings += align_warnings
   ```
   Keep the final `report.update({... "warnings": warnings ...})` merge and the 6-tuple return unchanged.
9. **Synthetic path.** `synthetic_foot.py` produces clean watertight meshes. Where the synthetic/demo flow cleans a mesh, pass `heavy_repair=False`. Confirm whether `run_demo.py`/`synthetic_foot.py` call `clean_scan` directly or only via `generate_orthotic`; if only via `generate_orthotic`, prefer the smallest change (an optional pass-through) that lets synthetic meshes skip close-holes/decimation. Leaving heavy repair on is safe (cheap on clean meshes) if a pass-through would bloat the change.

**Guardrails.** `clean_scan(mesh)` still works and returns one `trimesh.Trimesh`; new params are keyword defaults. `generate_orthotic`'s 6-tuple return is unchanged (`app.py` L96-98 unpacks `_, _, _, _, _, report`). No reorientation/mirroring/z-flooring in `clean_scan`. `MIN_THICKNESS_MM=2.0` clamps (L224/L226), the watertight solid build (L232-271), and `validate`'s checks (L276-288) must still pass on synthetic input. `run_demo.py` stays non-interactive (~12s). Report keys `side`, `mods_applied`, `warnings`, `foot_length_mm`, `output` preserved (additive only).

**Acceptance.**
1. `python -c "import pymeshlab; print(pymeshlab.__version__)"` succeeds.
2. `python run_demo.py` completes (~12s), writes STL(s) + preview PNG(s), `report["pass"] is True` with `checks.watertight` and `positive_volume` both True; synthetic path shows no spurious `clean_scan: closed ... holes` warnings.
3. **Holey-mesh check:** puncture a synthetic mesh (delete a face patch), run `clean_scan(mesh, heavy_repair=True, warnings=w)`; assert higher `is_watertight` / fewer boundary edges than input and a `clean_scan: closed ... holes` entry in `w`. Throwaway scratchpad script is fine — don't commit it or any generated output.
4. **Decimation budget:** input > `target_faces` → returned faces ≤ `target_faces` and a decimation warning; input under budget → no decimation, no warning.
5. **Skip path:** `clean_scan(mesh, heavy_repair=False)` calls neither close-holes nor decimation (verify by absent warnings) and returns a valid mesh.
6. **Regression:** `python app.py` starts; `/api/generate` on a synthetic scan returns `report` with `pass: True`.

**Effort: M.**

---

## 2. Add ICP-based scan alignment (adapt ampscan)

**Objective.** Add an iterative-closest-point (ICP) registration path that aligns a cleaned scan to a canonical template frame, used as a refinement on top of the PCA `align_canonical`. Vendor a minimal, MIT-attributed copy of ampscan's ICP rather than depending on the dormant PyPI package, and surface the alignment residual into the report warnings when it exceeds a threshold.

**Why.** PCA canonical alignment is brittle on real feet; ICP registration to a template is far more robust — and ampscan is MIT, so vendor the relevant module rather than depend on dormant releases.

**Files.**
- `geometry.py` — add ICP path; wire into `align_canonical`.
- `vendor/ampscan_icp.py` — **NEW.** Minimal vendored ICP (point-to-plane SVD + point-to-point), MIT header preserved, attributed to `github.com/abel-research/ampscan`.
- `vendor/__init__.py` — **NEW** (empty).
- `vendor/LICENSE.ampscan` — **NEW.** Verbatim ampscan MIT license text (copy exact text from upstream at vendor time).
- `templates/right_foot_template.stl` (or `.obj`) — **NEW.** Canonical right-foot template that MUST be in the internal frame (RIGHT-foot convention, +Y heel→toe, +X medial, +Z up, plantar min z=0, mm). If no real asset exists, generate one from `synthetic_foot.py` **and pass it through `align_canonical` (side="right") before checking it in** — raw `synthetic_foot.py` output is NOT guaranteed to be in the canonical frame, and ICP-registering real scans against a mis-framed template silently mis-aligns every part. Verify the checked-in mesh is in-frame (plantar min z≈0, correct axes) before committing. **Confirm the generator + `align_canonical` API in code.**
- `CLAUDE.md` — add one line to the Layout table for `vendor/` and `templates/`.

Do **not** add `ampscan` to `requirements.txt`.

**Approach.**
1. **Vendor the ICP core** in `vendor/ampscan_icp.py`: the rigid-registration math from ampscan's `align` class (`runICP`, `linPoint2Plane`, `linPoint2Point`, `getRMSE`, `getRT`). Strip the `AmpObject` dependency — operate on plain numpy arrays. Preserve the MIT header. Target:
   ```python
   # vendor/ampscan_icp.py  (adapted from ampscan, MIT — abel-research/ampscan)
   def register_icp(moving_v, moving_f, static_v, static_f,
                    method="linPoint2Plane", maxiter=20, inlier=1.0):
       """Returns (T 4x4 homogeneous, rmse float)."""
   ```
   Keep the `method` strings and `inlier` (0–1 proportion of nearest points) / `maxiter` semantics. Import only numpy/scipy/trimesh (already in use).
2. **Load/cache the template once.** In `geometry.py`, lazily `trimesh.load(..., force="mesh")` the template with a module-level cache so `run_demo.py`'s budget isn't hurt.
3. **Refinement function:**
   ```python
   ICP_RESIDUAL_WARN_MM = 3.0   # near CELL_MM (L23) / MIN_THICKNESS_MM (L24)

   def refine_icp(mesh, template) -> tuple[trimesh.Trimesh, float]:
       T, rmse = register_icp(mesh.vertices, mesh.faces, template.vertices, template.faces)
       out = mesh.copy(); out.apply_transform(T)
       return out, rmse
   ```
4. **PCA initializes, ICP refines.** Keep `align_canonical(mesh, side) -> (mesh, warnings)` (L57) as the initializer (it already mirrors left feet into the right convention, L61-63). **Prefer** running ICP against the right-foot template **inside `align_canonical`, before the origin re-zeroing (L109-111)**, so origin normalization runs last and downstream code is untouched.
5. **Gate + fall back to PCA.** If the ICP residual is worse than PCA-only, or `register_icp` raises, keep the PCA result. Append to the existing `warnings`:
   ```python
   if rmse > ICP_RESIDUAL_WARN_MM:
       warnings.append(f"ICP alignment residual {rmse:.1f}mm exceeds {ICP_RESIDUAL_WARN_MM}mm; flag for reviewer")
   ```
   This rides existing plumbing — no API changes.
6. **Optional feature flag:** `align_canonical(mesh, side, use_icp: bool = True)`, keyword-with-default so positional callers (`generate_orthotic` L295) still work.

**Guardrails.** `generate_orthotic` 6-tuple unchanged. `align_canonical` return type `(trimesh.Trimesh, list[str])` unchanged; ICP output stays a single connected mesh in the canonical frame with plantar floor z=0 (`Heightfield.__init__` L120 still works). Left-foot handling unchanged (mirror-back about X at L302, `volume<0` invert at L304). Don't touch `build_shell`/`apply_mods`/`solid_from_heightfield`/`validate` or the `MIN_THICKNESS_MM` clamps. No new hard dependency. Missing template → degrade gracefully to PCA, never crash. Preserve MIT text/attribution.

**Acceptance.**
- `python run_demo.py` completes, still emits STLs + PNGs, roughly same time.
- Report still shows `watertight=True`, `positive_volume=True`, plausible dims, `pass=True`.
- `report["warnings"]` present; a well-aligned demo scan has **no** ICP residual warning, but a perturbed input (or lowered `ICP_RESIDUAL_WARN_MM`) makes the `"ICP alignment residual ... exceeds ..."` warning appear.
- Since `align_canonical` returns `(mesh, warnings)` (no `rmse` in its signature), assert on the residual **warning** rather than a returned number: a misaligned input crosses `ICP_RESIDUAL_WARN_MM` and produces the `"ICP alignment residual ... exceeds ..."` entry in `report["warnings"]`, while a well-aligned input does not. (If you want a raw-number check that `rmse` decreases, print it from a direct `refine_icp`/`register_icp` call in a throwaway scratchpad script — not via `align_canonical`.)
- Left and right sides both produce correct-chirality output.
- `python -c "from vendor.ampscan_icp import register_icp"` imports with no `ampscan` installed.
- `grep -i "MIT" vendor/ampscan_icp.py` and presence of `vendor/LICENSE.ampscan` confirm attribution.

**Effort: M.**

---

## 3. Add optional per-zone lattice/fill to the Rx schema (defaults to solid)

**Objective.** Extend the Pydantic Rx contract in `schema.py` with an *optional* per-zone lattice/fill spec, and teach the `rx_parser.py` system prompt to emit it only on explicit variable-stiffness requests. **No geometry** — ship the validated contract plus documented default constants (zone→lattice map). Omitting fill must reproduce today's solid part identically.

**Why.** Lets the prescription drive per-zone stiffness (the differentiator) while staying 100% backward-compatible.

**Files.** `schema.py` (add fill models + constants) and `rx_parser.py` (extend `SYSTEM_PROMPT`, L22-41). No new files. Do **not** touch `geometry.py`, `app.py`, or `static/index.html`.

**Approach.**
1. **Enums** (reuse the `Literal`-alias style of `Landmark` on L8):
   ```python
   LatticeFamily = Literal["solid", "gyroid", "diamond", "primitive"]
   Zone = Literal["heel", "midfoot", "forefoot", "toe"]
   ```
   These anatomical zone names are **new** and distinct from the existing `Landmark` literals (`base_5th_met`, `met_heads`, `heel_center`, `arch_apex`, `hallux`). Keep both; do not merge or rename `Landmark`.
2. **`ZoneFill` model** (mirror the `Field(default, ge=, le=)` convention of `MedialWedge`/`Relief`):
   ```python
   class ZoneFill(BaseModel):
       family: LatticeFamily = "solid"
       density: float = Field(1.0, ge=0.0, le=1.0)   # 1.0 == fully solid
   ```
3. **Top-level `Fill` container**, per-zone (FootRx-level path — keeps fill out of the fixed-order `mods` pipeline and doesn't touch the `Mod` union):
   ```python
   class Fill(BaseModel):
       heel: ZoneFill = Field(default_factory=ZoneFill)
       midfoot: ZoneFill = Field(default_factory=ZoneFill)
       forefoot: ZoneFill = Field(default_factory=ZoneFill)
       toe: ZoneFill = Field(default_factory=ZoneFill)
   ```
   Use `default_factory` to avoid shared-instance aliasing. **Confirm Pydantic v2 is in use** (the union on L38 and `messages.parse(output_format=...)` in `rx_parser.py` both imply v2).
4. **Attach `Fill` as OPTIONAL on `FootRx`** (L41-42), default `None`:
   ```python
   class FootRx(BaseModel):
       mods: List[Mod] = []
       fill: Optional[Fill] = None   # None => solid everywhere (current behavior)
   ```
   `Optional` is already imported. Old `{"mods":[...]}` JSON still validates.
5. **Documented default zone→lattice map** (data only): Primitive=heel, Gyroid=forefoot, Diamond=toe+midfoot:
   ```python
   DEFAULT_ZONE_LATTICE: dict[str, str] = {
       "heel": "primitive", "midfoot": "diamond",
       "forefoot": "gyroid", "toe": "diamond",
   }
   ```
   A downstream geometry pass may consult this when a prescription requests variable stiffness without naming a family per zone. Must not change current defaults (default `Fill` is all-solid).
6. **Update `SYSTEM_PROMPT`** (L22-41). Add a rule to the "Rules:" list, mirroring how mods/landmarks/ranges are enumerated. It must instruct:
   - Emit `fill` (on `left`/`right`) **only** when the text explicitly calls for variable stiffness / softer-or-firmer zones / lattice / infill (e.g. "softer heel", "firmer forefoot"). Otherwise leave `fill` unset (part stays solid).
   - Allowed families `solid|gyroid|diamond|primitive`; `density` 0.0–1.0 where `1.0`=solid; zones `heel|midfoot|forefoot|toe`.
   - On unclear/contradictory/out-of-range fill requests, set `needs_manual=true` and record the reason in `ambiguities`. Never invent fill zones the text didn't call for.

   No change to `parse_prescription` — it delegates to `output_format=Prescription`; the new field flows through once schema + prompt are updated.

**Guardrails.** No signature changes in `geometry.py` (esp. `generate_orthotic` and its 6-tuple). Don't modify the `Mod` union (L38) or `apply_mods` order. Solid default mandatory: `fill=None` and every `ZoneFill` default reproduces current output (geometry ignores `fill` this item). Don't touch `MIN_THICKNESS_MM`/watertightness/`validate`. Old fill-absent JSON must still validate against `Prescription`/`FootRx`; confirm `parse_prescription` and `app.py`'s `FootRx(**req.rx)` accept both fill-absent and fill-present dicts. `run_demo.py` runs unchanged.

**Acceptance.**
1. `python -c "import schema; schema.FootRx(); schema.FootRx(**{'mods':[]}); print(schema.DEFAULT_ZONE_LATTICE)"` — no error; prints `heel=primitive, midfoot=diamond, forefoot=gyroid, toe=diamond`.
2. `python -c "from schema import FootRx; print(FootRx(**{'mods':[]}).model_dump()['fill'])"` prints `None`.
3. `FootRx(fill={'heel':{'family':'primitive','density':0.3}})` → ok; `density=1.5` raises `ValidationError`; `family='honeycomb'` raises `ValidationError`.
4. `python run_demo.py` completes (~12s), report `"pass": true` with the same checks — geometry unchanged.
5. Parser (needs `ANTHROPIC_API_KEY`, else inspect prompt statically): `"4 degree medial wedge both feet"` → `left.fill`/`right.fill` absent/None, `needs_manual=false`. `"softer heel, firmer forefoot"` populates `fill` on the relevant foot within range (else `needs_manual=true` + `ambiguities`).
6. `grep -n "fill\|LatticeFamily\|DEFAULT_ZONE_LATTICE" schema.py` and `grep -ni "fill\|lattice\|density" rx_parser.py` show the new contract + prompt rule.

**Effort: S.**

---

## 4. Variable-stiffness lattice interior generator (TPMS SDF + marching cubes)

**Objective.** Add a module that replaces the fully-solid orthotic interior with a watertight, printable TPMS lattice whose local volume-fraction follows a per-zone density field, smoothly blended across zone boundaries, while preserving the shell's outer surface and the `MIN_THICKNESS_MM` clamp. Solid fill stays the default and is the 100%-density limit of the same code path.

**Why.** Zoned lattices cut peak plantar pressure 36–42% vs uniform infill (Frontiers 2026); solid is the 100%-density end of the same dial.

**Depends on item 3's schema.** This section **consumes** the per-zone density field defined in `schema.py` (`FootRx.fill` → per-zone `ZoneFill{family, density}`); do not redefine it. **Confirm the final field name/shape in `schema.py` at implementation time.**

**Files.**
- **New:** `lattice.py` — all TPMS/SDF/marching-cubes logic. Importable, side-effect-free.
- `geometry.py` — call the module from `generate_orthotic` (L293-309) after `solid_from_heightfield`, before export/validate.
- `requirements.txt` — add `scikit-image` (`skimage.measure.marching_cubes`; NOT currently imported — geometry uses only numpy, trimesh, scipy.ndimage, scipy.interpolate.griddata) **and `manifold3d`** (the trimesh boolean backend used in step 6; current geometry uses no booleans, so no backend is guaranteed in the venv, and without one trimesh booleans raise or return empty → `is_watertight` fails on every lattice case). Confirm both install on the 3.11 `.venv`.

**Approach.**
1. **Entry point / no-op default:**
   ```python
   def apply_lattice(solid: trimesh.Trimesh, hf: Heightfield, top, mask, rx: FootRx) -> trimesh.Trimesh:
   ```
   `solid, hf, top, mask` are elements 1,3,4,5 of the `generate_orthotic` tuple. If `rx` has no lattice request — `fill` absent, OR every zone is `family=="solid"`, OR density 1.0 everywhere — **return `solid` unchanged** (a true no-op). Treat `family=="solid"` as forcing solid for that zone **regardless of its `density` value** (e.g. `solid`+0.3 is still solid), so `density` only bites for the TPMS families.
2. **Per-zone density field on the heightfield grid.** Produce `dens[j,i]` over `(hf.ny, hf.nx)` (same shape as `top`/`mask`), one value in `[0,1]` per masked cell. **The schema's `Zone` literals (`heel|midfoot|forefoot|toe`) are NOT valid landmark names** — `hf.landmark("midfoot")` raises `ValueError` (L152-171). Map each zone to a concrete landmark (or a longitudinal band) explicitly:
   ```python
   ZONE_TO_LANDMARK = {
       "heel":     "heel_center",
       "forefoot": "met_heads",
       "toe":      "hallux",
       "midfoot":  "arch_apex",   # if arch_apex is unavailable, use a yn()-band fallback
   }
   ```
   `hf.landmark(name)` returns `(row,col)` and only accepts `heel_center`, `base_5th_met`, `met_heads`, `arch_apex`, `hallux` (else `ValueError`). Seed each zone at its mapped landmark cell. For any zone lacking a clean landmark (notably `midfoot`), fall back to a `yn()`-style longitudinal band along +Y (heel→toe) rather than a point seed. Then **blend smoothly** with a Gaussian/EDT falloff — reuse `scipy.ndimage.gaussian_filter`, `scipy.ndimage.distance_transform_edt`, and the `_smoothstep(t)=t*t*(3-2t)` helper (L176-178). Density is per-XY-column, extruded constant in Z (thin-shell approximation — note as a limitation).
3. **Evaluate the TPMS implicit field on a 3D voxel grid.** Cover `solid.bounds` at pitch ≈ `CELL_MM/2` = 1.0 mm or finer; unit-cell period `L` tunable (6–10 mm). `np.meshgrid(..., indexing='ij')`, `k = 2*np.pi/L`, default gyroid:
   ```python
   F = np.sin(x)*np.cos(y) + np.sin(y)*np.cos(z) + np.sin(z)*np.cos(x)   # x,y,z = k*X,k*Y,k*Z
   ```
   Dispatch on the schema's `ZoneFill.family` literal (`solid|gyroid|diamond|primitive` — item 3 defines the field as **`family`, NOT `pattern`**; confirm in `schema.py` at implementation time): `gyroid` as above; `primitive` (Schwarz Primitive) `cos x + cos y + cos z`; `diamond` (Schwarz Diamond) `cos x*cos y*cos z - sin x*sin y*sin z`. `solid` is handled by the no-op / 100%-density limit (step 1), not meshed as a TPMS field. Any unexpected family falls back to gyroid with a recorded warning.
4. **Threshold to prescribed local density.** Thickened-strut form `solid_field = |F| - wall`, extract `level=0` (meshed region `|F| <= wall`). The wall↔density map is **nonlinear** and TPMS-type-specific — **calibrate numerically** (measure occupied-voxel fraction); do NOT assume a closed form. Approx gyroid anchors: `t 0.0→~50%`, `0.4→~35%`, `0.8→~20%`. Since density varies in XY, build a spatially-varying `wall[j,i]` interpolated onto the voxel grid so wall thickness tracks `dens` smoothly.
5. **Mesh the field:**
   ```python
   from skimage import measure
   verts, faces, normals, _ = measure.marching_cubes(solid_field, level=0.0, spacing=(px,py,pz))
   lattice_mesh = trimesh.Trimesh(vertices=verts, faces=faces, vertex_normals=normals)
   ```
   Offset `verts` by `solid.bounds[0]` into the solid's world frame. (Use unified `marching_cubes`, not the removed `_lewiner`/`_classic` names.)
6. **Intersect with the shell + cap for watertightness / outer surface / min-thickness.** The outer surface and `MIN_THICKNESS_MM=2.0` clamp are already baked into `solid` (via `apply_mods` L224/L226 and `solid_from_heightfield`). `result = solid.intersection(lattice_mesh)` so the outer skin/floor come from `solid` (this requires the `manifold3d` boolean backend added to `requirements.txt` above — verify `trimesh.boolean` picks it up in the venv; without a backend the intersection raises or returns empty). To guarantee a solid skin, union a thin inward-offset "wall shell" of `solid` with the intersected lattice so there is always ≥ `MIN_THICKNESS_MM` of solid at the boundary before the lattice begins. Verify `result.is_watertight`; run `result.fix_normals()` and invert if `result.volume < 0` (mirror the `solid_from_heightfield` convention).
7. **Wire into `generate_orthotic`.** After `solid = solid_from_heightfield(top, mask, hf)` and the left-foot mirror/volume-sign fixups (L302-304), insert `solid = apply_lattice(solid, hf, top, mask, rx)` **before** `solid.export(out_stl)` (so export and `validate(solid, hf)` see the lattice result). Keep the 6-tuple return unchanged.
8. **No GPL / microgen.** Do **NOT** import `microgen` or add it to `requirements.txt` — it is GPL, and pulling it into a distributed STL-generating device product is a copyleft trap. Use **only** the self-contained trimesh/skimage path specified above; it is fully sufficient here.

**Guardrails.** `generate_orthotic` 6-tuple unchanged (`app.py` L95-101 keeps only `report`). `solid_from_heightfield`/`build_shell`/`apply_mods`/`validate` signatures unchanged. **Solid is the default** — no lattice field → byte-for-byte current solid path (early return). Preserve watertightness and the `MIN_THICKNESS_MM=2.0` clamp (lattice never thins the outer skin below it). `validate` thresholds still apply: `positive_volume` = `volume>1000`, length 120-360mm (`ext[1]`), width 40-140mm (`ext[0]`), height ≤60mm (`ext[2]`). A lattice lowering `volume_cm3` / `est_print_mass_g_tpu` (= `volume/1000*1.22`) is expected/desirable but must stay `>1000` mm³. `run_demo.py` completes end-to-end (keep added time bounded); don't commit generated output. Don't modify `schema.py` beyond consuming the field; don't touch `rx_parser.py`.

**Acceptance.**
1. `python -c "import skimage.measure, trimesh; print('ok')"` succeeds.
2. **Solid default unchanged:** `python run_demo.py` with no lattice field → same STLs/report (`pass=True`, `checks.watertight=True`); confirm the no-op branch is hit (temp log or identical volume vs baseline).
3. **Lattice path:** run with a lattice-bearing rx → `report["checks"]["watertight"] is True`, `positive_volume is True`, `pass is True`.
4. **Density reduces mass:** same scan/shell → `volume_cm3` and `est_print_mass_g_tpu` strictly lower with sub-1.0 density than solid, and monotonically decrease as prescribed density drops.
5. **Numeric calibration:** a callable check (e.g. `python -m lattice --calibrate`) prints measured occupied-voxel fraction vs target for gyroid at a few thresholds within a stated tolerance (e.g. ±5% absolute) — proving t↔density was measured, not assumed.
6. **Smoothness:** after blending, assert max cell-to-cell density gradient below a set bound (spot-check a two-zone rx: heel dense, forefoot soft).
7. `result.is_watertight == True` and `result.volume > 1000` for every demo case; exported STL reloads (`trimesh.load(..., force="mesh").is_watertight`) and slices without manifold errors.

**Effort: L.**

---

## 5. Formalize audit + complaint record to FDA 21 CFR 820.35

**Objective.** Harden the inline `approve()` audit write in `app.py` into a proper append-only Device History Record (DHR) — one immutable JSONL event per approval capturing the full scan → prescription → geometry → validation → approval chain — and add a minimal complaint-intake endpoint with its own append-only complaint log keyed to `result_id`/`order_id`. Stay in-file (JSONL), matching the current `outputs/audit_log.jsonl` style.

**Why.** Custom foot orthotics are Class I / 510(k)-exempt but still owe recordkeeping + complaint files (820.35). The append-only `approve()` log is 80% there; formalizing it is cheap and a genuine regulatory/trust moat.

**Files.** `app.py` — factor a shared append helper, expand the DHR in `approve()` (L122-134), add complaint endpoints. New durable artifacts under `outputs/`: keep `audit_log.jsonl`, add sibling `complaints.jsonl` (both created lazily; dir already `mkdir(exist_ok=True)` at L25-29). No `schema.py`/`geometry.py`/`rx_parser.py` changes. Optional small `ComplaintRequest` Pydantic model in `app.py` next to `ParseRxRequest`/`GenerateRequest`.

**Approach.**
1. **Single append-only writer:**
   ```python
   def _append_event(path: Path, event_type: str, payload: dict) -> dict:
       rec = {"event_type": event_type,
              "ts_utc": datetime.now(timezone.utc).isoformat(),
              "prev_hash": _tail_hash(path), **payload}
       rec["record_hash"] = hashlib.sha256(
           json.dumps(rec, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
       with open(path, "a") as f:
           f.write(json.dumps(rec) + "\n")
       return rec
   ```
   Use `datetime.now(timezone.utc).isoformat()` — the current `time.strftime(...)` is local-time/naive, inadequate for a regulatory record. Keep the existing `approved_at` field if the UI reads it, but add the UTC ISO field as authoritative.
2. **Cheap hash chain.** `_tail_hash(path)` reads the last line's `record_hash` (or `"GENESIS"` if file absent/empty); each new record embeds it as `prev_hash` then hashes itself. Single-line tail read, stdlib only.
3. **Expand the DHR payload in `approve()`.** The current record writes 8 keys (`result_id, order_id, side, rx, shell, rx_text, report, approved_at`). `RESULTS[result_id]` (L103-108) already aggregates everything. Additionally capture:
   - **Scan identity** — from `r["scan"]`: `filename`, stored `path`, and `scan_sha256` (sha256 of the bytes at `scan["path"]`).
   - **Prescription provenance** — `rx_text` (raw), `rx` (parsed `FootRx.model_dump()`), and `parser_model` = `rx_parser.MODEL` (`"claude-opus-4-8"`, rx_parser.py L20 — import and record it). Note `generate()` accepts a pre-parsed `rx` dict and doesn't call `parse_prescription`, so record `parser_model` as best-effort and add a `# TODO` that a caller-supplied provenance flag would make this exact.
   - **Shell + mods** — `r["shell"]` (`Shell.model_dump()`) and the applied-mod order from `report["mods_applied"]`.
   - **Validation report** — the full `r["report"]` (`pass`, `checks`, `volume_cm3`, `extents_mm`, `warnings`, `foot_length_mm`, `generation_seconds`).
   - **Output STL identity** — `r["stl"]` path + `stl_sha256` (sha256 of the exported STL bytes).
   - **Reviewer approval** — `approved_at` (existing) + `ts_utc`; `"reviewer": None  # TODO: populate when auth lands`.
4. **Complaint intake endpoint** (mirror `approve()`/`get_stl()` — look up `RESULTS.get(result_id)`, 404 if missing):
   ```python
   class ComplaintRequest(BaseModel):
       narrative: str
       category: str = "unspecified"
       reporter: str = "anonymous"     # TODO: no PII handling / auth yet

   @app.post("/api/complaint/{result_id}")
   def file_complaint(result_id: str, req: ComplaintRequest):
       r = RESULTS.get(result_id)
       if not r:
           raise HTTPException(404, "Result not found")
       rec = _append_event(OUTPUTS / "complaints.jsonl", "complaint",
                   {"result_id": result_id, "order_id": r["order_id"],
                    "side": r["side"], "narrative": req.narrative,
                    "category": req.category, "reporter": req.reporter})
       return {"ok": True, "complaint_id": rec["record_hash"][:12]}
   ```
   Denormalize `order_id` from the result so complaints tie back to the DHR even after in-memory `RESULTS` is lost on restart.
5. **Read endpoints** (mirror `list_results()` L137, read-only, no delete/edit): e.g. `@app.get("/api/complaints")` and/or `@app.get("/api/device_history/{result_id}")` that stream matching JSONL lines.
6. **Document the SQLite migration path** in a comment where the writer lives (echo L33's "swap for SQLite when multi-user"): JSONL maps 1:1 to an `events` table (`event_type, ts_utc, prev_hash, record_hash, payload_json`) and a `complaints` table keyed by `result_id`; hash-chain columns port directly. Do not implement SQLite now.
7. **Auth/PII:** do NOT add auth or PII handling. Leave explicit `# TODO(820.198/Part 11): reviewer identity, signature, PII handling` markers at `reporter`, `reviewer`, and the endpoints.

**Guardrails.** No `generate_orthotic`/geometry signature changes — this only reads `report` and file paths. Don't alter frontend-facing response shapes: `approve()` still returns `{"ok": True, "download_url": ...}`; `generate()` still `{"result_id","report","stl_url"}`; `list_results()` unchanged. New fields go in the JSONL record, not HTTP responses (except the new complaint routes). Existing `outputs/audit_log.jsonl` lines must stay readable — readers tolerate old 8-key lines; treat pre-existing lines as `prev_hash="GENESIS"` predecessors (confirm desired behavior in code). Stdlib only — no `requirements.txt` changes. `run_demo.py` runs unchanged (no geometry/schema touched).

**Acceptance.**
- `python run_demo.py` still completes (~12s) with a passing report — geometry/schema untouched.
- Start `python app.py`; drive `/api/upload` → `/api/generate` → `/api/approve/{result_id}`. Then:
  - `outputs/audit_log.jsonl` gains exactly one line; it contains at minimum `event_type`, `ts_utc` (UTC ISO-8601 with offset), `scan` filename + `scan_sha256`, `rx_text`, `rx`, `parser_model == "claude-opus-4-8"`, `shell`, `report` (with nested `checks`), `stl_sha256`, `record_hash`, `prev_hash`.
  - `ts_utc` parses via `datetime.fromisoformat` and is timezone-aware.
- POST `/api/complaint/{result_id}` with `{"narrative":"arch pressure"}` → `{"ok":true,...}`; `outputs/complaints.jsonl` gains one line with `result_id`, `order_id`, `narrative`, `record_hash`. Bogus id → HTTP 404.
- Hash-chain check: edit a middle `narrative` in `complaints.jsonl`; a verifier loop (recompute each `record_hash`, compare to stored, confirm each `prev_hash` matches predecessor) reports a break at the tampered line, all prior lines intact.
- `grep -n "sqlite\|# TODO" app.py` shows the migration comment and auth/PII TODOs but no auth code.

**Effort: S.**

---

## Definition of done

- [ ] **Item 1:** `pymeshlab` imports (top-level only if the venv wheel is confirmed installed, else local to `clean_scan`); `clean_scan` runs the repair recipe with warnings threaded into `report["warnings"]`; holey-input smoke test shows improved watertightness; decimation budget and `heavy_repair=False` skip path both verified; `run_demo.py` and `/api/generate` still pass.
- [ ] **Item 2:** `vendor/ampscan_icp.py` (+ `__init__.py`, `LICENSE.ampscan`) vendored with MIT attribution; template asset checked in **and verified in-frame via `align_canonical`**; ICP refines after PCA inside `align_canonical`, falls back gracefully, and surfaces the residual warning when over threshold; both chiralities correct; `run_demo.py` green.
- [ ] **Item 3:** `schema.py` has `LatticeFamily`, `Zone`, `ZoneFill`, `Fill`, `DEFAULT_ZONE_LATTICE`, and optional `FootRx.fill=None`; range/enum validation enforced; old fill-absent JSON still validates; `SYSTEM_PROMPT` emits `fill` only on explicit requests; `run_demo.py` output unchanged.
- [ ] **Item 4:** `lattice.py` + `scikit-image`/`manifold3d` deps; `apply_lattice` is a true no-op for solid default (incl. `family=="solid"` regardless of density) and produces a watertight, min-thickness-preserving lattice for lattice rx; families dispatch on `ZoneFill.family`; zones map to real landmarks; mass drops monotonically with density; numeric density calibration and smoothness both proven; no GPL/microgen; `validate` stays green (`volume>1000`, watertight).
- [ ] **Item 5:** `_append_event` + hash chain; expanded DHR in `approve()` with scan/STL sha256, parser_model, full report; `/api/complaint/{result_id}` + read endpoints; UTC ISO timestamps; tamper detection works; stdlib only; existing HTTP response shapes unchanged.
- [ ] **Every item:** smallest change, no unrelated refactors, public signatures/return shapes preserved, `python run_demo.py` passes (`report["pass"] == True`, watertight, positive volume, plausible dims) before moving on; new deps added to `requirements.txt`; no generated `.stl`/`.obj`/PNG committed.