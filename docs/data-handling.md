# Data handling baseline

## Until production controls exist

- Use synthetic or explicitly de-identified data only.
- Do not place names, dates of birth, MRNs, addresses, contact details, free-text patient notes,
  credentials, or API keys in the repository, fixtures, filenames, logs, screenshots, or issues.
- Treat foot scans as potentially identifying clinical data even after obvious text fields are
  removed. Store them only in an approved location with restricted access.
- Do not expose the development server beyond localhost.
- Do not send prescriptions or scans to a cloud model until the organization confirms its legal,
  privacy, security, and contractual basis, including any required BAA.

## De-identification intake check

Before accepting a historical order, verify:

- The replacement order ID cannot be mapped back to a patient by the development team.
- Mesh metadata and filenames contain no identifiers.
- JSON keys and values contain no direct identifiers or unnecessary dates/free text.
- The prescription contains only the clinical instructions required for evaluation.
- The approved reference STL contains no embossed patient/order identifier.
- The source organization authorized this secondary use and transfer.

The replay harness strips a small denylist of obvious JSON keys and clears mesh metadata. That is
defense in depth, not a certification that the source is de-identified.

## Production controls still required

- Documented data-flow and threat model.
- Identity, role-based access, tenant/lab boundaries, and reviewer attribution.
- Encryption in transit and at rest with managed keys.
- Approved secret manager and key rotation.
- Durable audit trail, access logs, tamper detection, and time synchronization.
- Data retention, legal hold, export, deletion, backup, and restore procedures.
- Vendor inventory, risk assessment, agreements, and incident notification terms.
- Incident response, breach assessment, disaster recovery, and periodic access review.
- Test data management and a prohibition on copying production PHI into development.

Exact HIPAA, state-law, FDA, and quality-system obligations must be confirmed for the deployed
business and data flows by qualified advisors.
