# Rx parser backends

`rx_parser.parse_prescription` produces the same validated `Prescription` from a
pluggable inference backend. The Pydantic schema (`schema.py`) is the single
contract; only the inference path changes.

## Selecting a backend

Selection order: the `backend=` kwarg → the `ORTHOPIPE_RX_BACKEND` env var →
default `"anthropic"`.

| `ORTHOPIPE_RX_BACKEND` | backend | data leaves machine? | requires |
|---|---|---|---|
| _unset_ / `anthropic` | Anthropic Messages API (default) | yes (cloud) | `anthropic`, `ANTHROPIC_API_KEY` |
| `local` | Ollama, JSON-schema-constrained decode | **no** | `ollama` package + running Ollama server |

Extra knob: `ORTHOPIPE_RX_LOCAL_MODEL` picks the local model (default `llama3.1`).

```bash
# default cloud path (unchanged)
python -c "from rx_parser import parse_prescription; print(parse_prescription('4mm medial wedge left','ORD1').order_id)"

# fully local, PHI stays on the machine
ORTHOPIPE_RX_BACKEND=local ORTHOPIPE_RX_LOCAL_MODEL=llama3.1 \
  python -c "from rx_parser import parse_prescription; print(parse_prescription('softer heel, 4mm medial wedge, left foot','ORD2'))"
```

**Fail-loud, never silent-fallback:** an unknown backend name raises `ValueError`;
`local` selected without the `ollama` package or a reachable server raises
`RuntimeError` (and never constructs an Anthropic client). This is deliberate — a
PHI-safe deployment must never silently spill to the cloud.

## PHI on the cloud path (BAA)

Prescription free-text can carry PHI. If you must use the cloud backend with PHI,
confirm the current terms with Anthropic — as of writing:

- PHI is only permitted on the **Messages API under a signed Anthropic BAA**.
  The **Console/Workbench** and consumer **Pro/Team** plans are **not** covered by a BAA.
- **Covered Models** under the BAA require a **30-day retention** window, so
  **Zero-Data-Retention (ZDR) is not available** for them.

These are policy facts that change over time — **verify with Anthropic before
processing PHI**, and treat this doc as a pointer, not legal advice.

## PHI-safe default: local

The `local` backend runs entirely on-premise (Ollama / llama.cpp): no prescription
text leaves the machine, so a clinic can operate without a BAA. It uses the same
`SYSTEM_PROMPT`, with the model's decode constrained to the `Prescription` JSON
schema, then validated with `Prescription.model_validate_json`.

Optional deps are **not** in the default `requirements.txt` (see the commented
extras there); install `ollama` (and run a server) only for the local path.
