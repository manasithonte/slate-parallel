# src/api.py
import time
import asyncio
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from src.schemas import MediaJobRequest
from src.workers import (
    script_and_subtitle_worker,
    sound_stage_dubbing_worker,
    smart_reframing_vfx_worker,
    global_standards_compliance_worker
)
from src.orchestrator import synthesize_master_release_package

app = FastAPI(
    title="slate-parallel API",
    description="Agentic Post-Production Localization & Quality Control Orchestrator",
    version="1.0.0"
)

# Enable CORS for frontend dashboard calls
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
async def health_check():
    """Health check probe required for Google Cloud Run container readiness."""
    return {"status": "HEALTHY", "service": "slate-parallel", "version": "1.0.0"}

@app.post("/api/v1/process-media")
async def process_media_job(job_request: MediaJobRequest):
    """
    Primary Multi-Agent REST Endpoint:
    Fires 4 parallel workers concurrently and synthesizes a Master Release Package via Gemini.
    """
    start_time = time.time()
    
    try:
        # 1. FAN-OUT: Run all 4 workers concurrently
        worker_results = await asyncio.gather(
            script_and_subtitle_worker(job_request.script_text, job_request.target_languages),
            sound_stage_dubbing_worker(job_request.script_text, job_request.target_languages),
            smart_reframing_vfx_worker(job_request.video_url, job_request.target_platforms),
            global_standards_compliance_worker(job_request.script_text, job_request.target_languages)
        )
        
        # 2. FAN-IN SYNTHESIS: Generate Gemini QC Report
        master_package = await synthesize_master_release_package(job_request, worker_results)
        
        total_runtime = round(time.time() - start_time, 2)
        
        return {
            "status": "SUCCESS",
            "pipeline_runtime_seconds": total_runtime,
            "master_release_package": master_package
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Orchestration pipeline error: {str(e)}")