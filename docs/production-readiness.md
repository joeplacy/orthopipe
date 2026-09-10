# Production readiness plan

## Product boundary

OrthoPipe is intended to be an **internal fabrication-lab tool**. A trained reviewer remains in
the loop and owns approval. The software may translate clinical intent and generate geometry, but
it must not silently resolve ambiguous prescriptions or present a geometric check as proof of
clinical effectiveness.

The current repository is a validated engineering baseline, not a production release.

## Release gates

### Gate 0 — Reproducible engineering baseline

Status: **implemented locally; CI activates after the repository is pushed.**

- Imported work captured in version control.
- Python version and direct runtime dependencies pinned.
- One-command verification (`make verify`) and full synthetic demo (`make demo`).
- Unit/contract checks for schema ranges, mutable defaults, scale normalization, PII scrubbing,
  parser dispatch, health reporting, and replay path handling.
- GitHub Actions workflow for every pull request and main-branch push.
- Runtime host, port, data directory, and upload-size limit are configurable.

### Gate 1 — Fabricator workflow confirmed

Status: **partially documented.** The intake, Meshmixer orientation/layout, Simplify3D support,
toolpath review, and G-code handoff path are captured in
[`fabricator-workflow-current-state.md`](fabricator-workflow-current-state.md). The separate
prescription-modification procedure, timings, exception paths, acceptance rules, equipment, and
system ownership remain outstanding.

Observe real work rather than relying only on an interview. Complete
[`workflow-discovery.md`](workflow-discovery.md), including the happy path, exception paths,
handoffs, decision authority, artifacts, timings, queue states, rework, and acceptance criteria.

Exit evidence:

- Current-state swimlane reviewed by the people who do the work.
- Measured baseline for touch time, elapsed time, first-pass yield, remake rate, and queue age.
- Explicit list of decisions OrthoPipe may automate, recommend, or must leave to a reviewer.
- Scanner, CAD, slicer, printer, material, and job-tracking integration inventory.

### Gate 2 — Historical calibration and technical validation

Status: **waiting for de-identified representative orders and physical process inputs.**

Use the replay format in [`examples/deidentified-order/`](../examples/deidentified-order/) and
sample across common device/Rx types, operators, scan sources, and known difficult cases.

Exit evidence:

- Dataset inclusion/exclusion rules and provenance recorded.
- Landmark error and geometric deviation thresholds approved by domain experts.
- Parser evaluation uses real, de-identified prescriptions and real model outputs—not placeholder
  fixtures.
- Printed coupon testing maps lattice family/density to the actual TPU, printer, orientation, and
  slicer profile.
- Generated devices are compared with accepted devices and destructive/bench tests where needed.
- Failure cases reliably stop for manual review.

### Gate 3 — Controlled lab pilot

Status: **not started.**

- Durable database/object storage replaces the in-memory job registry.
- Authentication, roles, reviewer identity/signature, session management, and least privilege.
- TLS, secrets management, backup/restore, retention/deletion policy, and audit export.
- Malware/file validation, resource limits, worker isolation, retry/idempotency, and job recovery.
- Approved scanner ingestion, slicer profile, printer/material lot tracking, and output labeling.
- Monitoring covers job failures, queue age, validation failures, approval latency, and storage.
- Rollback, incident response, complaint handling, and support ownership are rehearsed.
- Pilot runs in shadow mode before any generated output is used operationally.

### Gate 4 — Production release

Status: **not started.**

- Pilot exit thresholds met for a pre-agreed sample size and time period.
- Risk management, software validation, change control, training, and work instructions approved.
- Regulatory counsel confirms classification, establishment obligations, quality-system scope,
  complaint/MDR handling, labeling, and records requirements for the actual business model.
- Security/privacy review and any required BAAs or other vendor agreements are complete.
- Named owners exist for product, clinical/fabrication validation, quality, security, operations,
  and incident response.

## Decisions intentionally deferred

These choices should follow workflow discovery rather than be baked into the prototype:

- Job/status data model and integrations.
- Whether the unit of work is a patient, encounter, order, pair, foot, device, or print job.
- Required reviewer roles and approval/signature sequence.
- Scanner and CAD interoperability priorities.
- Automatic slicer/printer dispatch versus reviewed export.
- Lab-specific geometry defaults and allowed adjustment ranges.

## Current technical limitations

- `SCANS` and `RESULTS` are process memory and are lost on restart.
- Audit and complaint records are local JSONL, not transactional or access-controlled.
- No authentication, authorization, tenant separation, electronic signatures, or TLS termination.
- Upload processing is synchronous and geometry work is not isolated in a durable job worker.
- Print preparation stops at a multi-body STL and manifest. It does not open a Simplify3D factory
  file, generate supports/G-code, or perform the required visual toolpath approval.
- Pressure output is a deterministic proxy, not validated FEA or clinical pressure evidence.
- Landmark rules and geometry have not been calibrated against representative real orders.
- Offline parser fixtures are placeholder emulations and must not be quoted as production accuracy.
- Dependency pins reproduce the current environment but still require vulnerability/license review
  and a controlled update policy.
- The project has no declared top-level software license; ownership and distribution terms must be
  decided before use outside the authorized development team.

## Production success measures

Set numeric thresholds with the fabricator at Gate 1. At minimum track:

- First-pass acceptance and remake rate.
- Median and p95 touch time and order elapsed time.
- Manual interventions and reason codes per order.
- Parser abstention/`needs_manual` recall on ambiguous prescriptions.
- Landmark and surface-deviation distributions by scan source and device type.
- Generation failure, validation failure, and approval-reversal rates.
- Print failure and material waste per accepted device.

No metric should be optimized without a balancing safety/quality metric.
