"""
OrthoPipe v1.1 — Review UI server.

Wraps geometry.generate_orthotic() behind a small web API + single-page UI:
  drag in a scan (.obj) -> set/edit prescription params -> Generate ->
  inspect 3D model + validation report -> Approve -> Download STL for the printer.

Run:  python app.py     (then open the forwarded port 8000 in the browser)
"""
import hashlib
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field as PydanticField

import rx_parser
from schema import FootRx, Shell
from geometry import generate_orthotic
from rx_parser import parse_prescription
from intake import normalize_scan

BASE = Path(__file__).parent
DATA_DIR = Path(os.environ.get("ORTHOPIPE_DATA_DIR", BASE)).expanduser().resolve()
UPLOADS = DATA_DIR / "uploads"
OUTPUTS = DATA_DIR / "outputs"
MAX_UPLOAD_BYTES = int(os.environ.get("ORTHOPIPE_MAX_UPLOAD_BYTES", str(100 * 1024 * 1024)))
DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOADS.mkdir(exist_ok=True)
OUTPUTS.mkdir(exist_ok=True)

app = FastAPI(title="OrthoPipe Review")

# in-memory job store (v1.1; swap for SQLite when multi-user)
SCANS: dict[str, dict] = {}
RESULTS: dict[str, dict] = {}

AUDIT_LOG = OUTPUTS / "audit_log.jsonl"       # Device History Record (DHR), append-only
COMPLAINTS_LOG = OUTPUTS / "complaints.jsonl"  # complaint file (820.198), append-only


# --- append-only, hash-chained event log (FDA 21 CFR 820.35 recordkeeping) ---
# JSONL now; maps 1:1 to SQLite when multi-user (echoing the store note above):
#   an `events` table (event_type, ts_utc, prev_hash, record_hash, payload_json)
#   and a `complaints` table keyed by result_id — the hash-chain columns port
#   directly. Do NOT implement SQLite yet.

def _sha256_file(path: str) -> str | None:
    """sha256 of a file's bytes, or None if it can't be read."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _tail_hash(path: Path) -> str:
    """record_hash of the last line (the chain head), or 'GENESIS' if none.
    Pre-existing 8-key audit lines have no record_hash -> treated as GENESIS
    predecessors, so old logs stay readable and the chain simply starts fresh."""
    if not path.exists():
        return "GENESIS"
    last = None
    with open(path, "rb") as f:
        for line in f:
            if line.strip():
                last = line
    if not last:
        return "GENESIS"
    try:
        return json.loads(last).get("record_hash", "GENESIS")
    except json.JSONDecodeError:
        return "GENESIS"


def _append_event(path: Path, event_type: str, payload: dict) -> dict:
    """Append one immutable, hash-chained event and return the written record."""
    rec = {"event_type": event_type,
           "ts_utc": datetime.now(timezone.utc).isoformat(),
           "prev_hash": _tail_hash(path), **payload}
    rec["record_hash"] = hashlib.sha256(
        json.dumps(rec, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    with open(path, "a") as f:
        f.write(json.dumps(rec) + "\n")
    return rec


@app.post("/api/upload")
async def upload_scan(file: UploadFile = File(...)):
    safe_name = Path(file.filename or "scan").name
    if not safe_name.lower().endswith((".obj", ".stl", ".ply", ".usdz")):
        raise HTTPException(400, "Please upload a .obj, .stl, .ply, or .usdz scan file")
    scan_id = uuid.uuid4().hex[:8]
    dest = UPLOADS / f"{scan_id}_{safe_name}"
    payload = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(payload) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"Scan exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit")
    dest.write_bytes(payload)
    # phone-scan intake: usdz->obj (if USD tooling present) + meters->mm scale check.
    # Returns an already-normalized path; /api/generate needs no change.
    try:
        norm_path, warnings = normalize_scan(str(dest))
    except ValueError as e:            # e.g. usdz without USD tooling -> guidance, HTTP 400
        raise HTTPException(400, str(e))
    SCANS[scan_id] = {"path": norm_path, "filename": safe_name,
                      "uploaded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                      "warnings": warnings}
    return {"scan_id": scan_id, "filename": safe_name, "warnings": warnings}


class ParseRxRequest(BaseModel):
    rx_text: str
    order_id: str = PydanticField("ORDER", min_length=1, max_length=80,
                                  pattern=r"^[A-Za-z0-9_-]+$")


@app.post("/api/parse_rx")
def parse_rx(req: ParseRxRequest):
    """Free-text prescription -> structured Prescription draft (Claude-parsed).

    Output is schema-validated and range-clamped; the reviewer still edits and
    approves it in the UI before anything is generated.
    """
    if not req.rx_text.strip():
        raise HTTPException(400, "Prescription text is empty")
    try:
        rx = parse_prescription(req.rx_text, req.order_id)
    except Exception as e:
        raise HTTPException(502, f"Rx parser failed: {e}")
    return rx.model_dump()


class GenerateRequest(BaseModel):
    scan_id: str
    side: str                       # "left" | "right"
    order_id: str = PydanticField("ORDER", min_length=1, max_length=80,
                                  pattern=r"^[A-Za-z0-9_-]+$")
    rx: dict                        # FootRx-shaped: {"mods": [...]}
    shell: dict = PydanticField(default_factory=dict)  # Shell-shaped
    rx_text: str = ""               # raw prescription text, stored for audit


@app.post("/api/generate")
def generate(req: GenerateRequest):
    scan = SCANS.get(req.scan_id)
    if not scan:
        raise HTTPException(404, "Scan not found — upload it first")
    if req.side not in ("left", "right"):
        raise HTTPException(400, "side must be 'left' or 'right'")
    try:
        foot_rx = FootRx(**req.rx)
        shell = Shell(**req.shell)
    except Exception as e:
        raise HTTPException(422, f"Invalid prescription parameters: {e}")

    result_id = uuid.uuid4().hex[:8]
    stl_path = OUTPUTS / f"{req.order_id}_{req.side}_{result_id}.stl"
    t0 = time.time()
    try:
        _, _, _, _, _, report = generate_orthotic(
            scan["path"], req.side, foot_rx, shell, str(stl_path))
    except Exception as e:
        raise HTTPException(500, f"Geometry engine failed: {e}")
    report["generation_seconds"] = round(time.time() - t0, 1)

    RESULTS[result_id] = {
        "result_id": result_id, "order_id": req.order_id, "side": req.side,
        "scan": scan, "rx": foot_rx.model_dump(), "shell": shell.model_dump(),
        "rx_text": req.rx_text, "report": report, "stl": str(stl_path),
        "approved": False, "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    return {"result_id": result_id, "report": report,
            "stl_url": f"/api/stl/{result_id}"}


@app.get("/api/stl/{result_id}")
def get_stl(result_id: str):
    r = RESULTS.get(result_id)
    if not r:
        raise HTTPException(404, "Result not found")
    fname = f"{r['order_id']}_{r['side']}.stl"
    return FileResponse(r["stl"], media_type="model/stl", filename=fname)


@app.post("/api/approve/{result_id}")
def approve(result_id: str):
    r = RESULTS.get(result_id)
    if not r:
        raise HTTPException(404, "Result not found")
    r["approved"] = True
    r["approved_at"] = time.strftime("%Y-%m-%d %H:%M:%S")  # kept for the UI (local, naive)

    # Full Device History Record: the whole scan -> Rx -> geometry -> validation
    # -> approval chain, captured as one immutable, hash-chained event.
    scan = r["scan"]
    dhr = {
        "result_id": result_id, "order_id": r["order_id"], "side": r["side"],
        # scan identity
        "scan": {"filename": scan["filename"], "path": scan["path"],
                 "scan_sha256": _sha256_file(scan["path"])},
        # prescription provenance
        "rx_text": r["rx_text"], "rx": r["rx"],
        # parser_model is best-effort: generate() takes a pre-parsed rx dict and
        # never calls parse_prescription, so this records which model the parser
        # stage uses, not necessarily the one that produced *this* rx.
        # TODO: a caller-supplied provenance flag on GenerateRequest would make this exact.
        "parser_model": rx_parser.MODEL,
        # shell + applied-mod order
        "shell": r["shell"], "mods_applied": r["report"].get("mods_applied", []),
        # full validation report (pass, checks, volume_cm3, extents_mm, warnings, ...)
        "report": r["report"],
        # output STL identity
        "stl": r["stl"], "stl_sha256": _sha256_file(r["stl"]),
        # reviewer approval
        "approved_at": r["approved_at"],
        # TODO(820.198/Part 11): reviewer identity, signature, PII handling
        "reviewer": None,
    }
    _append_event(AUDIT_LOG, "device_history_record", dhr)
    return {"ok": True, "download_url": f"/api/stl/{result_id}"}


class ComplaintRequest(BaseModel):
    narrative: str
    category: str = "unspecified"
    # TODO(820.198/Part 11): no PII handling / auth yet
    reporter: str = "anonymous"


@app.post("/api/complaint/{result_id}")
def file_complaint(result_id: str, req: ComplaintRequest):
    """Log a complaint against a device result (21 CFR 820.198 complaint file)."""
    r = RESULTS.get(result_id)
    if not r:
        raise HTTPException(404, "Result not found")
    # denormalize order_id/side so complaints tie back to the DHR even after the
    # in-memory RESULTS store is lost on restart.
    rec = _append_event(COMPLAINTS_LOG, "complaint",
                        {"result_id": result_id, "order_id": r["order_id"],
                         "side": r["side"], "narrative": req.narrative,
                         "category": req.category, "reporter": req.reporter})
    return {"ok": True, "complaint_id": rec["record_hash"][:12]}


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    with open(path) as f:
        for line in f:
            if line.strip():
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return out


@app.get("/api/complaints")
def list_complaints():
    """Read-only complaint file stream (no edit/delete — append-only record)."""
    return _read_jsonl(COMPLAINTS_LOG)


@app.get("/api/device_history/{result_id}")
def device_history(result_id: str):
    """Read-only Device History Record(s) for one result."""
    return [rec for rec in _read_jsonl(AUDIT_LOG) if rec.get("result_id") == result_id]


@app.get("/api/results")
def list_results():
    return [{k: r[k] for k in ("result_id", "order_id", "side", "approved", "created_at")}
            for r in RESULTS.values()]


# --- replay scorecard surface (read-only; populated by `replay.py --out outputs/replay`) ---
REPLAY_DIR = OUTPUTS / "replay"


def _find_scorecard(order_id: str) -> Path | None:
    if (not order_id or len(order_id) > 80 or
            any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
                for c in order_id)):
        return None
    if not REPLAY_DIR.exists():
        return None
    hits = sorted(REPLAY_DIR.glob(f"{order_id}_*_scorecard.json"))
    return hits[0] if hits else None


@app.get("/api/replay/{order_id}")
def get_replay(order_id: str):
    """Return an order's geometric-diff scorecard + heatmap URL (read-only)."""
    sc = _find_scorecard(order_id)
    if sc is None:
        raise HTTPException(404, "No replay scorecard for this order_id")
    card = json.loads(sc.read_text())
    return {"scorecard": card, "heatmap_url": f"/api/replay/{order_id}/heatmap"}


@app.get("/api/replay/{order_id}/heatmap")
def get_replay_heatmap(order_id: str):
    """Serve the deviation heatmap PNG for an order (mirrors /api/stl FileResponse)."""
    sc = _find_scorecard(order_id)
    if sc is None:
        raise HTTPException(404, "No replay scorecard for this order_id")
    heatmap_name = Path(json.loads(sc.read_text()).get("heatmap_png", "")).name
    png = (REPLAY_DIR / heatmap_name).resolve()
    if png.parent != REPLAY_DIR.resolve() or png.suffix.lower() != ".png" or not png.exists():
        raise HTTPException(404, "Heatmap not found")
    return FileResponse(str(png), media_type="image/png", filename=png.name)


@app.get("/api/health")
def health():
    """Process-level readiness probe. Clinical readiness is reported per generated part."""
    return {"status": "ok", "service": "orthopipe"}


app.mount("/", StaticFiles(directory=BASE / "static", html=True), name="static")

if __name__ == "__main__":
    uvicorn.run(app,
                host=os.environ.get("ORTHOPIPE_HOST", "127.0.0.1"),
                port=int(os.environ.get("ORTHOPIPE_PORT", "8000")))
