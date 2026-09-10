# OrthoPipe — Landscape & Strategy

_Compiled from two adversarially-verified deep-research passes (46 sources, 330+ claims
extracted, 34 findings that survived 3-vote verification). Figures reflect sources as of
July 2026. Decision inputs, not legal or clinical advice._

A polished, shareable version of this report is published as an Artifact:
https://claude.ai/code/artifact/f71536a0-9d9a-4176-8b23-24b1d323fd7b

---

## The strategic read

**Your core bet is validated — but the wedge is narrower and sharper than "automation."**

Scan → print automation is **table stakes**: Materialise Phits, Aetrex/SOLS, HP Arize, and
Denmark's Sequence all ship it, some with pressure-plate fusion and "reorder in three clicks."
Competing on "we automate design" walks into incumbents.

The defensible combination — which **no competitor pairs together** — is:

1. **LLM-parsed free-text prescriptions** (every rival uses fixed forms),
2. a **deterministic, range-clamped, human-approved geometry engine with a full audit trail**, and
3. **per-zone variable-stiffness lattice interiors** driven by that prescription — which the newest
   peer-reviewed work shows cut peak plantar pressure **36–42%** vs uniform infill.

Reframe the pitch from "we automate insole design" to **"we turn a prescription sentence into a
validated, pressure-optimized, printable part with a traceable record from words to STL."** That's
a claim about trust and clinical intent, not just throughput — the one thing form-driven black-box
pipelines can't easily copy.

---

## 1 · Competitive landscape

Two axes decide the field: how prescriptions **enter** (manual form → AI/free-text), and who **owns
the geometry** (hand-CAD → fully automated). OrthoPipe's open water is automated geometry **plus**
language-native intake, with auditability as the differentiator.

| Player | What it is | Intake | Pricing (verified) | Gap you exploit |
|---|---|---|---|---|
| **Wiky** (wikyapps.com) | Cloud podiatry platform: scan → design → manufacturing hand-off. The named target. | 4-step **fill-out form**; no AI/NLP in its marketing | pay-per-scan (undisclosed) | No free-text/LLM Rx; design is template/manual CAD |
| **Materialise Phits** | Footscan pressure plates + 3D scan drive **multi-density** insoles; mature EU Class IIa. | Form + pressure; SAM iOS scan is an *adjunct* (plate still required) | enterprise (undisclosed) | Hardware-heavy; no language intake; closed design logic |
| **Aetrex / SOLS** | B2B scan-to-print: Albert 2 Pro → EOS printing, 2–3 wk. Acquired SOLS 2017. | Scanner-driven; no local geometry control | B2B service | Outsourced & opaque; no in-clinic iteration or audit |
| **HP Arize** | Fully outsourced: scan → submit to HP → inserts mailed back. | Scan only; no local design/print | per-device | Zero clinician control; pilot RCT favored *traditional* at 8 wks |
| **Sequence** (Denmark) | **Closest AI rival:** ML on 20,000+ measurements predicts target shore hardness & shape from scan + gait, 10 pressure zones. | Scan + dynamic pressure → ML | commercial | Data-driven, not *intent*-driven; no clinician-language channel |
| **Gensole** (Gyrobot, UK) | Closest free tool: browser designer, morphs upper to scan ("Solemorph"), density zones via slicer. | Manual; **explicitly no Rx corrections** — deform scan by hand first | free, non-commercial; 2016-era, decaying | Its own docs admit the exact gap your Rx engine fills |
| **OrthoCAD** (Vertex) | Traditional orthotic CAD workstation software. | Manual CAD | **$1,500/yr** start, up to $12,999 perpetual | Pure manual CAD; the "software-only" price floor |

**Scanner ecosystem note:** Structure Sensor Pro (~$1,000, widely adopted in O&P) already ships
**automatic 8-landmark foot detection** in SDK 3.5.0 (acropodion, pternion, heel, 1st/last met, met
fibulare/tibiale, arch apex) — a COTS alternative to building landmarking yourself. COMB and
Materialise SAM both prove **phone-scan-only** (TrueDepth) intake in production.

---

## 2 · Open-source head start (license-aware)

You plan to ship a product, so: permissive (MIT/Apache) is drop-in, GPL is **server-side-only** safe
(GPLv3 has no network clause), "unknown" is a stop sign until the LICENSE file is read directly.

| Component | License | Does | Verdict |
|---|---|---|---|
| **PyMeshLab** (cnr-isti-vclab) | GPL-3.0 | Close-holes, decimate, smooth via MeshLab | **Adopt now.** Already in `requirements.txt`, not yet called. Unblocks real (hole-riddled) scans. Server-side use avoids copyleft. |
| **ampscan** (abel-research) | **MIT** | P&O scan analysis; ICP rigid alignment | **Vendor & adopt.** ICP > PCA for template-to-scan registration. Dormant since 2023 → copy the module in. |
| **CadQuery** | **Apache-2.0** | Parametric B-rep CAD (OCCT); STEP/DXF/STL/AMF/**3MF** | **Adopt** for crisp posts/trims the heightfield can't express, and 3MF export to the slicer. Safest license. |
| **microgen** (3MAH) | GPL-3.0 | TPMS gyroid/Diamond/Primitive via SDF + marching cubes; CAD-optional in 2.0 | **Strong, server-side.** Matches the zone→lattice research exactly. Pulls VTK/gmsh. |
| **Scaffolder / PyScaffolder** (nodtem66) | **unverified** | Mesh → TPMS gyroid | **Hold.** MIT claim failed verification; **no macOS wheels**. Read LICENSE first; else prefer microgen. |
| **trimesh SDF lattice** (build-your-own) | yours | Gyroid/Diamond/Primitive as analytic implicit fields | **Clean long-term play.** ~150 lines, no license entanglement, full gradient control. Prototype with microgen, then internalize. |

---

## 3 · Academic findings that should change the engine

Current engine = heightfield shell + flat bottom. The literature points to a **shell wrapping a
zoned, variable-stiffness lattice interior** — plus some assumptions to un-learn.

1. **Zoned lattices beat uniform infill — measurably.** Partition-infilled TPMS cut peak plantar
   pressure **~36% static / ~42% dynamic gait** vs uniform gyroid (30–37% lower mean in gait).
   _(Frontiers Bioeng. 2026)_ → **The single biggest product lever.** Shell + per-zone lattice.
2. **A concrete zone → lattice map.** Stiffness ranks **Diamond > Gyroid > Primitive**. Soft
   **Primitive → heel** (deform, spread contact), **Gyroid → forefoot**, stiff **Diamond → toe +
   midfoot**. → Bake as the default map in `apply_mods`; let Rx nudge it.
3. **Blend stiffness smoothly, don't step it.** Graded TPU gyroids last longest under fatigue with
   moderate, smooth gradients; abrupt jumps degrade fatigue life. _(Mech. Adv. Mater. Struct. 2025,
   cushioning study — extrapolated)_ → Interpolate lattice density across Rx zone boundaries.
4. **Gyroid isn't automatically best.** In TPU, **honeycomb** gave the highest compression load at
   50% strain and most energy absorption; **gyroid was lowest**. → Make lattice *family* a schema
   parameter, not a hardcoded gyroid.
5. **Bench stiffness ≠ in-gait pressure.** Honeycomb vs gyroid at 20% infill showed no significant
   in-shoe pressure difference vs no insole; an n=21 pilot even saw *increased* met pressure. → Add a
   pressure-based validation check (simulated or plate-fused); geometric pass ≠ clinical proof.
6. **"3D-printed" is not automatically better.** A pilot RCT favored **traditional** over HP Arize at
   8 wks (PROMIS function 49.0 vs 41.9). → Compete on speed, cost, traceability, and
   pressure-optimization — not material superiority.

---

## 4 · US regulatory footing

Custom foot orthotics are generally **Class I, 510(k)-exempt** — no premarket submission. Nuances:

**What holds**
- 510(k)-exempt, Class I.
- **GMP-lite:** exempt from most of 21 CFR 820 *except* recordkeeping & complaint files (§820.35).
  Your append-only audit log is the right instinct — extend to complaint handling.
- You're **"patient-matched," not "custom"** — FDA treats scan-fit-within-cleared-specs devices as
  patient-matched → normal (exempt) path, *not* the Custom Device Exemption.

**Where it bites**
- **Don't lean on the Custom Device Exemption** (§520(b)): capped at **5 units/yr per device type** —
  useless for volume.
- **Central fabrication = you're a manufacturer** (registration + GMP-lite + MDR reporting). Only
  dispensing-only retail O&P is registration-exempt (§807.65(i)) — not you.
- **Confirm the exact product code.** §890.3025 (accessories) does *not* classify a foot orthosis;
  the real code is a shoe-insert / arch-support classification.

**PHI angle on the Rx parser:** prescriptions can carry PHI. Sign a **BAA** for the Claude API and/or
ship a **local-LLM fallback** — a local Llama-70B extracted explicit clinical features at **95–100%
sensitivity**, so on-prem parsing is realistic where cloud is forbidden.

_Not legal advice. Confirm the FDA product code and consult a regulatory specialist before market._

---

## 5 · Head-start moves, ranked by impact ÷ effort

| # | Move | Effort | Impact | Why |
|---|---|---|---|---|
| 1 | **Wire PyMeshLab into `clean_scan`** | low | high | Already a dep, unused. Real scans are hole-riddled; #1 blocker to real data. |
| 2 | **Adopt ICP alignment from ampscan** | low | med-high | ICP > PCA for registration on real feet. MIT; vendor the module. |
| 3 | **Formalize audit + complaint record to §820.35** | low | med | You're 80% there; regulatory moat + trust story. |
| 4 | **Local-LLM fallback + Claude BAA** | med | med | De-risks the one AI dependency legally (PHI). |
| 5 | **Per-zone variable-stiffness lattice interior** | high | high | **The product.** Primitive-heel / Gyroid-forefoot / Diamond-toe-midfoot, smooth gradients. The 36–42% pressure claim, realized. Prototype with microgen, then trimesh SDF. |
| 6 | **Surface lattice family + density in the Rx schema** | med | high | Lets the LLM Rx actually drive the geometry the literature calls for. Extends your schema-as-contract. |
| 7 | **Add pressure-based validation** | high | high | Move "validation pass" from geometric to clinical. Closes bench≠gait gap; a review-UI edge over Phits. |
| 8 | **Phone-scan-only intake path** | med | med | COMB/SAM prove TrueDepth-only works. Removes scanner lock-in Aetrex/HP impose. |
| 9 | **Own the provenance story** | low | med | "Every part traceable from Rx sentence to STL." Nearly free; incumbents' black boxes can't answer it. |

---

## Method & open questions

**Method.** Two fan-out passes: parallel web search (5 angles) → source fetch & claim extraction →
**3-vote adversarial verification** (survives only if not refuted). 34 findings cleared the bar.
Dropped claims include a competitor's export formats and an unverifiable scanner-ecosystem detail.

**Still open — verify before betting the company:**
- The **exact FDA product code** for the device (drives the whole regulatory analysis).
- **Scaffolder's real license** — read the LICENSE file before adopting.
- Whether **any competitor beyond Wiky** ships free-text Rx (confirmed absent only for Wiky).
- Quantitative **lattice-density → shore-hardness** mapping in *your* TPU — validate on printed coupons.

### Key sources
- Frontiers Bioeng. 2026 — partition-infill TPMS · 10.3389/fbioe.2026.1820362
- Mech. Adv. Mater. Struct. 2025 — graded TPU gyroids · 10.1080/15376494.2025.2530146
- Foot & Ankle Ortho. 2026 — traditional vs HP Arize RCT · 10.1177/24730114251413246
- Preprints 2025 — TPU infill compression · 10.20944/preprints202503.0894.v1
- Sequence ML pipeline · 10.1177/11795972251371476
- Local-LLM clinical extraction · 10.1101/2023.12.07.23299648
- wikyapps.com/podiatry-wiky-solution · materialise.com/en/healthcare/phits-suite
- aetrexb2b.com/pages/3d-printing · gensole.com
- github: abel-research/ampscan · 3MAH/microgen · cnr-isti-vclab/PyMeshLab · cadquery/cadquery
- structure.io — automatic foot landmark detection (SDK 3.5.0)
- FDA: 21 CFR 890.3025 · §807.65(i) · custom device exemption §520(b)
