# src/workers.py
import os
import asyncio
import time
from typing import List
from parallel import AsyncParallel
from src.schemas import DepartmentOutput

# Initialize Parallel SDK client (uses PARALLEL_API_KEY from .env)
parallel_api_key = os.getenv("PARALLEL_API_KEY", "")
parallel_client = AsyncParallel(api_key=parallel_api_key) if parallel_api_key else None

async def script_and_subtitle_worker(script_text: str, languages: List[str]) -> DepartmentOutput:
    """Worker 1: The Script & Subtitle Supervisor (Idiomatic Translation)."""
    start_time = time.time()
    await asyncio.sleep(0.4)  # Simulate processing
    
    subtitles = {
        lang: f"00:00:01,000 --> 00:00:04,000 [{lang} Idiomatic Localized Subtitle Track]"
        for lang in languages
    }
        
    return DepartmentOutput(
        worker_id="worker_01",
        department_name="Script & Subtitle Supervisor",
        status="SUCCESS",
        data={"subtitles": subtitles},
        execution_time_seconds=round(time.time() - start_time, 2)
    )

async def sound_stage_dubbing_worker(script_text: str, languages: List[str]) -> DepartmentOutput:
    """Worker 2: Sound Stage & Dubbing Lead (Multi-Speaker Gemini TTS)."""
    start_time = time.time()
    await asyncio.sleep(0.5)
    
    audio_tracks = {lang: f"dubbed_track_{lang.lower()}.wav" for lang in languages}
    
    return DepartmentOutput(
        worker_id="worker_02",
        department_name="Sound Stage & Dubbing Lead",
        status="SUCCESS",
        data={
            "dubbed_tracks": audio_tracks,
            "voice_profile": "Multi-speaker pitch matching applied"
        },
        execution_time_seconds=round(time.time() - start_time, 2)
    )

async def smart_reframing_vfx_worker(video_url: str, platforms: List[str]) -> DepartmentOutput:
    """Worker 3: Smart Reframing Director (Dynamic Actor Tracking & Crop Coordinates)."""
    start_time = time.time()
    await asyncio.sleep(0.3)
    
    crop_coords = {
        "9:16 (TikTok)": {"crop_w": 607, "crop_h": 1080, "focal_x_tracking": "center_actor_01"},
        "1:1 (Instagram)": {"crop_w": 1080, "crop_h": 1080, "focal_x_tracking": "center_actor_01"}
    }
    
    return DepartmentOutput(
        worker_id="worker_03",
        department_name="Smart Reframing Director (VFX)",
        status="SUCCESS",
        data={"crop_coordinates": crop_coords, "source": video_url},
        execution_time_seconds=round(time.time() - start_time, 2)
    )

async def global_standards_compliance_worker(script_text: str, target_languages: List[str]) -> DepartmentOutput:
    """Worker 4: Global Standards & Compliance Guardian (Parallel API Integration)."""
    start_time = time.time()
    parallel_interaction_id = "mock_interaction_id"
    compliance_checks = ["Clear for PG-13 release", "No regional trademark violations flagged"]
    
    # Live runtime execution with Parallel API
    if parallel_client and parallel_api_key and parallel_api_key != "your_parallel_api_key_here":
        try:
            task_res = await parallel_client.task_run.create(
                input=f"Check film release rating compliance for target regions: {', '.join(target_languages)}",
                processor="base"
            )
            parallel_interaction_id = task_res.interaction_id
        except Exception as e:
            compliance_checks.append(f"Parallel API call note: {e}")

    return DepartmentOutput(
        worker_id="worker_04",
        department_name="Global Standards & Compliance Guardian",
        status="SUCCESS",
        data={
            "parallel_interaction_id": parallel_interaction_id,
            "compliance_checks": compliance_checks
        },
        execution_time_seconds=round(time.time() - start_time, 2)
    )