"""
OrthoPipe v1.1 — Review UI server.

Wraps geometry.generate_orthotic() behind a small web API + single-page UI:
  drag in a scan (.obj) -> set/edit prescription params -> Generate ->
  inspect 3D model + validation report -> Approve -> Download STL for the printer.

Run:  python app.py     (then open the forwarded port 8000 in the browser)
"""
import json
import time
import uuid
from pathlib import Path

import uvicorn
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from schema import FootRx, Shell
from geometry import generate_orthotic

BASE = Path(__file__).parent
UPLOADS = BASE / "uploads"
OUTPUTS = BASE / "outputs"
UPLOADS.mkdir(exist_ok=True)
OUTPUTS.mkdir(exist_ok=True)

app = FastAPI(title="OrthoPipe Review")

# in-memory job store (v1.1; swap for SQLite when multi-user)
SCANS: dict[str, dict] = {}
RESULTS: dict[str, dict] = {}


@app.post("/api/upload")
async def upload_scan(file: UploadFile = File(...)):
    if not file.filename.lower().endswith((".obj", ".stl", ".ply")):
        raise HTTPException(400, "Please upload a .obj, .stl, or .ply scan file")
    scan_id = uuid.uuid4().hex[:8]
    dest = UPLOADS / f"{scan_id}_{file.filename}"
    dest.write_bytes(await file.read())
    SCANS[scan_id] = {"path": str(dest), "filename": file.filename,
                      "uploaded_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    return {"scan_id": scan_id, "filename": file.filename}


class GenerateRequest(BaseModel):
    scan_id: str
    side: str                       # "left" | "right"
    order_id: str = "ORDER"
    rx: dict                        # FootRx-shaped: {"mods": [...]}
    shell: dict = {}                # Shell-shaped
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
    r["approved_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    # append-only audit log — regulatory posture from day one
    with open(OUTPUTS / "audit_log.jsonl", "a") as f:
        f.write(json.dumps({k: r[k] for k in
                            ("result_id", "order_id", "side", "rx", "shell",
                             "rx_text", "report", "approved_at")}) + "\n")
    return {"ok": True, "download_url": f"/api/stl/{result_id}"}


@app.get("/api/results")
def list_results():
    return [{k: r[k] for k in ("result_id", "order_id", "side", "approved", "created_at")}
            for r in RESULTS.values()]


app.mount("/", StaticFiles(directory=BASE / "static", html=True), name="static")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
