# Fabricator current state workflow

## Scope and confidence

This process map translates the fabricator's `Foot Orthotic Workflow.docx`, received September 10,
2026. It records what the document states and separates implementation inferences from confirmed
facts. The source contains no timings, screenshots, prescriptions, scans, finished orthoses,
printer details, or separate modification procedure.

## Confirmed workflow

| State | Confirmed activity | System or artifact | OrthoPipe status |
|---|---|---|---|
| Received | SplintFab sends scans by email; FOS uses oral/written notification | Email or notification | External intake; integration deferred |
| Files staged | A patient folder is created and scans are downloaded | OneDrive for SplintFab; Dropbox plus Comb Portal for FOS | Manual; PHI/source-of-truth controls unresolved |
| Scans oriented | Each scan is imported and oriented so plantar heel and metatarsals touch the Meshmixer bed as closely as possible | Meshmixer project | Bed-contact metric now reported; tolerance remains provisional |
| Design complete | Modifications are executed per specification for both orthoses | Separate workflow not yet supplied | Geometry exists, but equivalence is unvalidated |
| Print layout ready | Forefeet point upward, medial sides face the printer center, lowest points meet the bed, trimlines are close but not touching, and objects are combined/exported | Meshmixer multi-body export | Implemented as configurable `print_prep.py` output |
| Supports generated | Pair is imported into a Simplify3D factory file, moved `-0.3 mm` in Z, and automatic supports use a `23°` threshold | Simplify3D `.factory` file | Values recorded; slicing not implemented |
| Toolpath QC approved | Fabricator visually observes prepared G-code | Simplify3D preview | Required human gate; criteria unknown |
| G-code released | G-code is saved in the patient folder and emailed to SplintFab | G-code, folder, email | External release; deliberately blocked in current code |

## Implemented interpretation

`print_prep.py` applies one explicit interpretation of the repeatable Meshmixer operations:

- Canonical heel-to-toe `+Y` is rotated to vertical `+Z` so the forefoot points upward.
- Generated left and right geometry is placed on opposite sides of the centerline so both medial
  faces point inward.
- The requested space between axis-aligned bounds is enforced and verified.
- The pair is exported as one STL containing two disconnected bodies. It is not boolean-unioned.
- The documented `0.3 mm` bed embed and `23°` support threshold are stored in the manifest.
- Optional printer build dimensions can turn bed fit into an actual check once supplied.

The exact rotation and packing must be compared with the requested real completed FO or a prepared
Meshmixer/Simplify3D screenshot before this interpretation is treated as validated.

## Confirmed filename and data behavior

The current filename embeds patient ID, description/modification shorthand, `FO MM`, and date. This
is unsuitable for development fixtures and may expose PHI when files are emailed or copied. The
OrthoPipe implementation uses a constrained non-identifying order ID and records hashes and
transform matrices in a separate manifest.

## Required next evidence

Highest value first:

1. One de-identified foot scan and its corresponding completed FO ready for print.
2. The separate Meshmixer modification workflow referenced by the source document.
3. The Simplify3D `.factory` file or screenshots of the imported, supported pair and toolpath.
4. Printer model/build volume, nozzle, material, layer settings, and print orientation rationale.
5. The fabricator's visual G-code approval and finished-part acceptance criteria.
6. Clarification of FOS and which system is authoritative for order status and released files.
