# Fabricator workflow discovery guide

The first written workflow has been mapped in
[`fabricator-workflow-current-state.md`](fabricator-workflow-current-state.md). Use this guide to
fill its known gaps rather than asking the fabricator to repeat the documented steps.

Use this during direct observation of at least one routine order and one difficult/rework order.
Do not record patient identifiers. Capture screenshots or sample files only after they are
de-identified and approved for use.

## Session information

- Lab / location:
- Date:
- Participants and roles:
- Order types observed:
- Approximate daily/weekly volume:
- Peak periods or backlog pattern:

## Current-state step table

Create one row for every action, wait, handoff, or decision. Record measured time where possible.

| # | Trigger/input | Person/role | Action and decision | Tool/equipment | Output/status | Touch time | Wait time | Failure/rework path |
|---|---|---|---|---|---|---:|---:|---|
| 1 | | | | | | | | |

For each step ask:

- What information is required before starting, and where does it come from?
- What do you inspect or measure? What causes you to stop or ask a clinician?
- Which values are rules, defaults, experience-based judgments, or lab-specific conventions?
- What gets typed or transferred again? Which formats and naming conventions are used?
- How do you know the step is complete? Who may override or approve it?
- What commonly goes wrong, and how is the problem discovered and corrected?

## Artifacts and systems inventory

| Artifact/system | Format/version | Source of truth | Contains PHI? | Created by | Consumed by | Retention |
|---|---|---|---|---|---|---|
| Prescription | | | | | | |
| Scan | | | | | | |
| CAD design | | | | | | |
| Slicer profile | | | | | | |
| Print job | | | | | | |
| Inspection/approval | | | | | | |
| Complaint/remake | | | | | | |

Record scanner model/export settings, CAD software/version, slicer/version/profile, printer model,
nozzle, orientation, TPU brand/grade/lot, post-processing, and measurement equipment.

## Decision inventory

Classify each decision only after the fabricator explains it.

| Decision | Inputs/evidence | Allowed range | Who decides | Frequency | Consequence if wrong | OrthoPipe role |
|---|---|---|---|---:|---|---|
| | | | | | | automate / recommend / require review / out of scope |

Pay special attention to landmark corrections, trim line, posting, heel cup, relief placement,
thickness, accommodation, material/stiffness, scan quality, left/right identity, and remake triage.

## Exceptions and rework

For each common exception capture frequency, detection point, responsible role, added time,
disposition, and whether the original data/output is preserved. Ask for anonymized examples of:

- Bad or incomplete scans.
- Contradictory/illegible prescriptions.
- Unusual anatomy or device combinations.
- CAD changes after review.
- Failed prints or post-processing defects.
- Fit complaints, clinician changes, and remakes.

## Acceptance criteria

Ask the fabricator to demonstrate—not only describe—how a part is accepted. Record objective
measurements separately from visual/tactile judgment. Include who can release a part, what is
recorded, and what evidence must remain linked to the order.

## Baseline measures

Collect a representative baseline before introducing automation:

- Touch and wait time by step.
- First-pass yield and remake rate.
- Orders per operator per day.
- Queue age and oldest-order age.
- Manual CAD adjustments per order and reason.
- Print failures, material consumed, and scrap.

## Close-out

Return a one-page current-state swimlane, system/artifact map, exception list, decision inventory,
baseline metrics, and prioritized pain points to participants for correction. Treat silence as
unreviewed, not approval.
