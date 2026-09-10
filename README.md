# OrthoPipe

OrthoPipe is a production-oriented internal tool for orthotics fabrication labs. It turns a
3D foot scan plus a structured or free-text prescription into a validated, reviewer-approved,
print-ready orthotic STL with a traceable record from intake through approval.

The repository is an engineering prototype, **not yet a production or clinically validated
system**. Synthetic geometry passes today; real-world calibration, workflow validation, access
control, durable storage, and printer/material process validation remain release gates. See
[`docs/production-readiness.md`](docs/production-readiness.md).

## What exists

- Scan cleanup, canonical alignment, heightfield shell generation, prescription modifications,
  variable-stiffness lattice support, geometric checks, and a pressure proxy.
- FastAPI review station for upload, prescription review, generation, approval, download,
  complaint capture, and replay scorecards.
- Cloud and explicitly selected local Rx-parser backends.
- Historical-order replay/diff harness and landmark calibration tools.
- Offline Rx evaluation fixtures and a stdlib regression suite.
- De-identification guards for replay inputs and phone-scan normalization helpers.

## Local setup

Requires Python 3.11. The dependency versions in `requirements.txt` are pinned to the validated
baseline.

```bash
make setup
make verify
make demo
```

To start the local review station:

```bash
# Use .env.example as a reference; export overrides in your shell/process manager.
make serve
```

The server binds to `127.0.0.1:8000` by default. `ORTHOPIPE_DATA_DIR` moves runtime uploads and
outputs outside the source tree. Do not expose the server to a network until authentication,
authorization, TLS, and durable audit storage are implemented.

## Common commands

| Command | Purpose |
|---|---|
| `make verify` | Unit tests, parser self-check/offline evaluation, replay identity check |
| `make demo` | Full synthetic scan → STL run for both feet |
| `make replay-synthetic` | Five-order replay/diff exercise in `outputs/replay/` |
| `python -m eval.score_rx --offline` | Score cached parser fixtures without an API key |
| `python -m landmarks --help` | Landmark measurement/calibration commands |

## Rx parsing and PHI

Geometry and offline evaluation require no model API key. Live parsing defaults to Anthropic and
requires `ANTHROPIC_API_KEY`; the local Ollama path is selected explicitly with
`ORTHOPIPE_RX_BACKEND=local`. Never commit keys or paste patient-identifying data into fixtures.
Before real PHI is processed, complete the data-handling controls in
[`docs/data-handling.md`](docs/data-handling.md) and confirm appropriate vendor agreements.

## Historical calibration input

The replay harness consumes one directory per de-identified order containing:

- `order.json` — non-identifying order ID, side, and optional shell settings
- `rx.json` — structured prescription
- `scan.obj`, `scan.stl`, or `scan.ply` — original scan geometry
- `reference.stl` — fabricator-approved final device

Start with [`examples/deidentified-order/README.md`](examples/deidentified-order/README.md).

## Repository map

| Path | Role |
|---|---|
| `schema.py` | Validated prescription contract |
| `geometry.py` | Cleanup → alignment → shell → mods → validation → export |
| `lattice.py` | Optional zoned lattice generation |
| `rx_parser.py`, `rx_backends.py` | Free-text prescription parsing |
| `app.py`, `static/index.html` | Review-station API and UI |
| `replay.py` | Historical replay and geometric scorecards |
| `landmarks.py` | Landmark measurement and calibration |
| `eval/` | Offline parser evaluation |
| `tests/` | Fast regression and safety tests |
| `docs/` | Strategy, discovery, data handling, and readiness gates |

## Product direction

The intended operating model is an internal tool for fabrication labs, with production readiness
rather than a demo as the objective. Fabricator workflow discovery and representative historical
orders are acknowledged inputs to later gates; they are not silently replaced with assumptions.
