# src/api.py
import time
import asyncio
import uuid
from pathlib import Path
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from src import gcs_engine
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
OUTPUT_UPLOADS_DIR = Path("outputs") / "uploads"
OUTPUT_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"}
MAX_UPLOAD_BYTES = 200 * 1024 * 1024

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


@app.post("/api/v1/upload-video")
async def upload_video(file: UploadFile = File(...)):
    """Accept a video file upload and return a video_url usable directly by
    /api/v1/process-media and /api/v1/assemble-final. Staged to GCS when
    configured — a gs:// URI works correctly no matter which Cloud Run
    instance later handles those requests, the same reasoning behind the
    render-job Firestore persistence. Falls back to a local path otherwise,
    which only works for single-process local dev."""
    original_name = Path(file.filename or "upload.mp4").name
    extension = Path(original_name).suffix.lower()
    if extension not in ALLOWED_VIDEO_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported video file type: {extension or 'unknown'}")

    temp_path = OUTPUT_UPLOADS_DIR / f"{uuid.uuid4().hex}{extension}"
    total_bytes = 0
    try:
        with open(temp_path, "wb") as out:
            while chunk := await file.read(1024 * 1024):
                total_bytes += len(chunk)
                if total_bytes > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=400, detail="Video exceeds the 200 MB upload limit")
                out.write(chunk)
    except HTTPException:
        temp_path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        temp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Upload failed: {exc}")

    if gcs_engine.is_gcs_configured():
        try:
            video_url = await asyncio.to_thread(
                gcs_engine.upload_to_gcs, str(temp_path), f"uploads/{temp_path.name}"
            )
        finally:
            temp_path.unlink(missing_ok=True)
        return {"video_url": video_url}

    return {"video_url": str(temp_path)}

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
    if not request.platform_renditions:
        # submit_render_job builds one RenderOutput per platform — with none
        # given, that's an empty outputs list, which _run_render_pipeline
        # then marks FAILED with no per-output error to explain why (there's
        # nowhere to attach one). Reject it clearly here instead.
        raise HTTPException(status_code=400, detail="No platforms selected — pick at least one target platform before rendering.")
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
        dubbing_segments = {
            lang: segments
            for lang, segments in worker_01.data.get("localized_segments", {}).items()
            if lang in job_request.dubbing_languages
        }
        worker_02 = await sound_stage_dubbing_worker(
            job_request.title,
            dubbing_segments,
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
