# OrthoPipe

Geometry engine prototype: scan `.obj` + prescription JSON → validated, print-ready orthotic STL.
Full context lives in [README.md](README.md); this file is machine-setup/orientation for Claude Code.

## Setup

Requires **Python 3.10+** (`pymeshlab` won't install on the system Python 3.9). This repo uses a
`.venv` built with Homebrew's `python@3.11`:

```bash
source .venv/bin/activate
```

If the venv doesn't exist yet:

```bash
/opt/homebrew/bin/python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Running things

```bash
python run_demo.py   # geometry engine only — generates STLs + preview PNGs in repo root, ~12s
python app.py         # FastAPI review UI on http://localhost:8000
```

`app.py` serves the UI from `static/index.html` (copied from the root `index.html` — keep both
in sync until the duplication is cleaned up upstream).

## Layout

| File | Role |
|---|---|
| `schema.py` | Pydantic Rx schema — the LLM↔geometry contract |
| `geometry.py` | Cleanup → alignment → heightfield → shell → mods → validate → export |
| `synthetic_foot.py` | Fake scan generator (dev only) |
| `run_demo.py` | End-to-end CLI demo |
| `rx_parser.py` | Claude-powered free-text Rx → `Prescription` (needs `ANTHROPIC_API_KEY`) |
| `app.py` | FastAPI review-station backend (incl. `/api/parse_rx`) |
| `static/index.html` | Review-station frontend (served by `app.py`) |
| `vendor/` | Vendored third-party code (MIT ampscan ICP core), kept out of `requirements.txt` |
| `templates/` | Canonical foot template(s) for ICP alignment (in-frame, plantar z=0) |

## Notes

- No test suite yet — `run_demo.py` is the closest thing to an integration check.
- `uploads/` and `outputs/` are created at runtime by `app.py`; gitignored.
- Generated `.stl`/`.obj`/preview PNGs are gitignored — don't commit demo output.
