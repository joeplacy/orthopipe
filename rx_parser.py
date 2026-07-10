"""
Rx parser: free-text prescription -> validated Prescription object, via Claude.

This is the only place AI touches the pipeline. The model's entire job is to
emit a `Prescription` (schema.py); the SDK validates the response against the
Pydantic schema, so nothing unvalidated ever reaches the geometry engine.
If the text is unclear, the model must flag `needs_manual` / `ambiguities`
rather than guess — a human reviews everything in the review station anyway.

Requires ANTHROPIC_API_KEY in the environment.

CLI check:  python rx_parser.py "Bilateral medial wedges, 1/4in heel lift right only"
"""
import sys

import anthropic

from schema import Prescription

MODEL = "claude-opus-4-8"

SYSTEM_PROMPT = """\
You parse clinician prescriptions for custom foot orthotics into structured JSON.

Rules:
- Emit ONLY parameters supported by the schema. Available modifications:
  medial_wedge (degrees), lateral_wedge (degrees), heel_lift (height_mm),
  relief (landmark + depth_mm + radius_mm), met_pad (height_mm).
  Landmarks: base_5th_met, met_heads, heel_center, arch_apex, hallux.
- "Bilateral" / "both feet" means the mod goes on BOTH left and right.
- Convert units to millimeters (1 inch = 25.4 mm). E.g. ".25in heel lift" -> 6.35 mm.
- If a numeric value is not stated, omit it so the schema default applies —
  except heel_lift, where height_mm is required; if a lift is requested with
  no height, set needs_manual=true and record the ambiguity.
- If any part of the prescription is unclear, contradictory, references an
  unsupported modification, or requests a value outside safe ranges
  (wedges 0-8 degrees, lifts 0-15mm, reliefs 0.5-4mm deep), do NOT guess:
  set needs_manual=true and describe each problem in `ambiguities`.
- Shell options (thickness, trim style, heel cup) only if explicitly stated.
- Never invent modifications that were not prescribed.
"""


def parse_prescription(rx_text: str, order_id: str = "ORDER") -> Prescription:
    """Parse free-text prescription into a validated Prescription.

    Raises anthropic.APIError on API failures and pydantic.ValidationError if
    the response cannot be validated (the SDK retries schema mismatches).
    """
    client = anthropic.Anthropic()
    response = client.messages.parse(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": f"Order ID: {order_id}\n\nPrescription text:\n{rx_text}",
        }],
        output_format=Prescription,
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("Model declined to process this prescription text")
    rx = response.parsed_output
    rx.order_id = order_id  # authoritative — comes from the work order, not the model
    return rx


if __name__ == "__main__":
    text = " ".join(sys.argv[1:]) or (
        "Bilateral Medial Wedges, .25in Heel lift on Right only. "
        "Lateral base of 5th relief, both feet"
    )
    print(parse_prescription(text, "CLI-TEST").model_dump_json(indent=2))
