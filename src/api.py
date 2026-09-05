# src/api.py
import time
import asyncio
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from src.schemas import MediaJobRequest, RenderJob, RenderJobRequest
from src.workers import (
    run_script_subtitle_worker,
    sound_stage_dubbing_worker,
    smart_reframing_vfx_worker,
    global_standards_compliance_worker
)
from src.orchestrator import synthesize_master_release_package
from src.render_engine import submit_render_job, get_render_job

app = FastAPI(
    title="slate-parallel API",
    description="Agentic Post-Production Localization & Quality Control Orchestrator",
    version="1.0.0"
)

OUTPUT_AUDIO_DIR = Path("outputs") / "audio"
OUTPUT_SUBTITLE_DIR = Path("outputs") / "subtitles"

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


@app.get("/api/v1/downloads/audio/{filename}")
async def download_dubbed_audio(filename: str):
    """Download a generated localized MP3 as an attachment."""
    safe_filename = Path(filename).name
    if safe_filename != filename or not safe_filename.endswith(".mp3"):
        raise HTTPException(status_code=400, detail="Invalid audio filename")
    audio_path = OUTPUT_AUDIO_DIR / safe_filename
    if not audio_path.is_file():
        raise HTTPException(status_code=404, detail="Dubbed audio file not found")
    return FileResponse(audio_path, media_type="audio/mpeg", filename=safe_filename)

@app.get("/api/v1/downloads/subtitle/{filename}")
async def download_subtitle(filename: str):
    """Download a generated localized .srt subtitle file as an attachment."""
    safe_filename = Path(filename).name
    if safe_filename != filename or not safe_filename.endswith(".srt"):
        raise HTTPException(status_code=400, detail="Invalid subtitle filename")
    subtitle_path = OUTPUT_SUBTITLE_DIR / safe_filename
    if not subtitle_path.is_file():
        raise HTTPException(status_code=404, detail="Subtitle file not found")
    return FileResponse(subtitle_path, media_type="application/x-subrip", filename=safe_filename)

@app.post("/api/v1/assemble-final", response_model=RenderJob)
async def assemble_final_release(request: RenderJobRequest):
    """
    Final-assembly stage: submits one Google Cloud Transcoder job per
    (platform x language) pair, combining the crop, dubbed audio, and
    subtitles from a prior /api/v1/process-media run into a rendered .mp4.
    Returns immediately after submission; poll /api/v1/render-status/{id}
    for completion. Falls back to a NOT_CONFIGURED status if GCS/Transcoder
    aren't set up, rather than failing the request.
    """
    try:
        return await submit_render_job(request)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Render submission error: {str(e)}")


@app.get("/api/v1/render-status/{render_job_id}", response_model=RenderJob)
async def get_render_status(render_job_id: str):
    """Poll the status of a submitted final-assembly render job."""
    job = await get_render_job(render_job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Render job not found")
    return job


@app.post("/api/v1/process-media")
async def process_media_job(job_request: MediaJobRequest):
    """
    Primary Multi-Agent REST Endpoint:
    Fires 4 parallel workers concurrently and synthesizes a Master Release Package via Gemini.
    """
    start_time = time.time()
    
    try:
        # Worker 01 translates for the union of both language pools — dubbing
        # needs translated text same as subtitles do — so a language picked
        # only for dubbing (not subtitles) still gets a translation to dub.
        all_languages = list(dict.fromkeys(
            job_request.dubbing_languages + job_request.subtitle_languages
        ))

        # 1. FAN-OUT: Run all 4 workers concurrently
        worker_01, worker_03, worker_04 = await asyncio.gather(
            run_script_subtitle_worker(
                job_request.video_url,
                job_request.script_text,
                all_languages,
                job_request.title,
            ),
            smart_reframing_vfx_worker(job_request.video_url, job_request.target_platforms),
            global_standards_compliance_worker(job_request.script_text, all_languages),
        )
        # Worker 02 only synthesizes audio for languages actually requested
        # for dubbing, even though worker_01 translated a possibly larger set.
        dubbing_dialogues = {
            lang: text
            for lang, text in worker_01.data.get("localized_dialogues", {}).items()
            if lang in job_request.dubbing_languages
        }
        worker_02 = await sound_stage_dubbing_worker(
            job_request.title,
            dubbing_dialogues,
        )
        worker_results = [worker_01, worker_02, worker_03, worker_04]
        
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
