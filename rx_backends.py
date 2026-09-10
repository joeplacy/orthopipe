"""
rx_backends.py — pluggable inference backends for the Rx parser (batch-2 item 5).

Each backend takes the same (system, user, output_format, model) and returns a
validated `Prescription`. The Pydantic schema is the single contract across
backends. `rx_parser.parse_prescription` selects one via a kwarg / env var and
keeps the authoritative `order_id` override outside the backend.

All heavy clients are lazy-imported INSIDE their function so importing this module
(and `rx_parser`) never requires `anthropic`, `ollama`, or `llama-cpp-python`.
"""
from __future__ import annotations
import os

from schema import Prescription   # schema import is light (pydantic only)

MAX_TOKENS = 16000


def anthropic_backend(system: str, user: str, output_format, model: str) -> Prescription:
    """Current cloud behavior, verbatim: schema-validated `messages.parse`."""
    import anthropic   # lazy; keeps rx_parser importable without the SDK on the path
    client = anthropic.Anthropic()
    response = client.messages.parse(
        model=model,
        max_tokens=MAX_TOKENS,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_format=output_format,
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("Model declined to process this prescription text")
    return response.parsed_output


def local_backend(system: str, user: str, output_format, model: str) -> Prescription:
    """Fully-local backend (Ollama): JSON-schema-constrained decode + manual validate.

    No data leaves the machine — the PHI-safe path. `model` (the Anthropic model id)
    is ignored; the local model is chosen via ORTHOPIPE_RX_LOCAL_MODEL (default
    'llama3.1'). Fails loud if the client/server is unavailable — never falls back
    to the cloud.
    """
    local_model = os.environ.get("ORTHOPIPE_RX_LOCAL_MODEL", "llama3.1")
    try:
        from ollama import chat   # lazy optional dep
    except ImportError as e:
        raise RuntimeError(
            "ORTHOPIPE_RX_BACKEND=local requires the 'ollama' package and a running "
            "Ollama server (pip install ollama; https://ollama.com)") from e
    try:
        resp = chat(
            model=local_model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            format=output_format.model_json_schema(),   # constrains tokens to the schema
            options={"temperature": 0},
        )
    except Exception as e:
        raise RuntimeError(
            f"ORTHOPIPE_RX_BACKEND=local: Ollama server unreachable or model "
            f"'{local_model}' unavailable ({e})") from e
    # llama.cpp alternative (not shipped): LlamaGrammar.from_json_schema(...) as grammar=,
    # or llama-server response_format={"type":"json_schema",...}. SYSTEM_PROMPT stays the
    # provider-agnostic prompt since the schema constrains tokens but is not shown.
    return output_format.model_validate_json(resp.message.content)


# name -> backend; the dispatcher in rx_parser reads this
BACKENDS = {"anthropic": anthropic_backend, "local": local_backend}
