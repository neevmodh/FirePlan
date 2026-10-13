from __future__ import annotations

import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles

from .ingestion import ingest_dxf
from .models import OptimizationRequest
from .optimizer import optimize

app = FastAPI(title="FirePlan", version="0.2.0", description="CAD import and one hydraulic-constrained genetic optimization workspace.")
pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="fireplan")
jobs: dict = {}
lock = threading.Lock()
UPLOAD_LIMIT = 4 * 1024 * 1024


@app.get("/api/health")
def health():
    return {"status": "ok", "hydraulics": "WNTRSimulator", "optimizer": "Genetic algorithm", "version": "0.2.0"}


class Cancelled(Exception):
    pass


def execute_job(job_id, request, cancel):
    def progress(update):
        if cancel.is_set():
            raise Cancelled()
        with lock:
            jobs[job_id]["progress"] = update
    try:
        result = optimize(request.network, request.settings, progress)
        with lock:
            jobs[job_id].update(status="cancelled" if cancel.is_set() else "complete",
                                result=None if cancel.is_set() else result)
    except Cancelled:
        with lock:
            jobs[job_id]["status"] = "cancelled"
    except Exception:
        logging.exception("Optimization failed for job %s", job_id)
        with lock:
            jobs[job_id].update(status="failed", error="Could not solve this network. Check CAD geometry and water inputs.")


@app.post("/api/optimize", status_code=202)
def start_job(request: OptimizationRequest):
    with lock:
        if any(job["status"] == "running" for job in jobs.values()):
            raise HTTPException(429, "An optimization is already running. Wait for it to finish.")
        for old_id in list(jobs):
            if time.monotonic() - jobs[old_id]["created"] > 3600:
                del jobs[old_id]
        while len(jobs) >= 20:
            del jobs[next(iter(jobs))]
        job_id = uuid.uuid4().hex
        cancel = threading.Event()
        jobs[job_id] = {"status": "running", "created": time.monotonic(), "cancel": cancel,
                        "progress": {"evaluated": 0, "coverage_pct": 0, "evaluation_budget": request.settings.evaluation_budget}}
    pool.submit(execute_job, job_id, request, cancel)
    return {"job_id": job_id, "status": "running"}


@app.get("/api/jobs/{job_id}")
def read_job(job_id: str):
    with lock:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "Job not found. Import and optimize again.")
        return {key: value for key, value in job.items() if key not in {"cancel", "created"}}


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    with lock:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "Job not found")
        if job["status"] == "running":
            job["cancel"].set()
        return {"status": "cancellation_requested" if job["status"] == "running" else job["status"]}


@app.post("/api/import")
async def import_plan(file: UploadFile = File(...), metres_per_unit: float = Form(1),
                      source_x: float | None = Form(None), source_y: float | None = Form(None), diameter_mm: float = Form(100)):
    content = await file.read(UPLOAD_LIMIT + 1)
    if len(content) > UPLOAD_LIMIT:
        raise HTTPException(413, "File exceeds 4 MB")
    if Path(file.filename or "").suffix.lower() != ".dxf":
        raise HTTPException(415, "Import an ASCII DXF CAD file. Convert DWG to DXF first.")
    try:
        network = ingest_dxf(content, metres_per_unit=metres_per_unit, source_x=source_x,
                             source_y=source_y, pipe_layer="PIPES", diameter_mm=diameter_mm)
        network.name = Path(file.filename).stem[:120] or "Imported CAD plan"
        return network
    except ValueError as error:
        raise HTTPException(422, str(error)[:2000]) from error


app.mount("/", StaticFiles(directory=Path(__file__).parent.parent / "web", html=True), name="web")
