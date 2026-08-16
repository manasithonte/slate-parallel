# src/main.py
import asyncio
import time
import json
from dotenv import load_dotenv
from src.schemas import MediaJobRequest
from src.workers import (
    script_and_subtitle_worker,
    sound_stage_dubbing_worker,
    smart_reframing_vfx_worker,
    global_standards_compliance_worker
)
from src.orchestrator import synthesize_master_release_package
# Place at the very top of src/main.py
import os
from dotenv import load_dotenv

load_dotenv()  # Loads GOOGLE_APPLICATION_CREDENTIALS and GOOGLE_CLOUD_PROJECT from .env

load_dotenv()

async def run_slate_parallel():
    print("🎬 [SHOWRUNNER AGENT] Initializing 'slate-parallel' Orchestrator Pipeline...\n")
    
    # Producer Request Payload
    job_request = MediaJobRequest(
        title="Inception 2 Teaser",
        script_text="Protagonist: We have to go deeper into the subconscious layer.",
        video_url="https://storage.googleapis.com/sample-bucket/raw_trailer.mp4",
        target_languages=["Japanese", "Spanish"],
        target_platforms=["TikTok (9:16)", "Instagram (1:1)"]
    )
    
    start_time = time.time()
    print(f"⚡ [FAN-OUT] Triggering 4 Department Workers concurrently for '{job_request.title}'...")
    
    # 1. FAN-OUT: Trigger all 4 workers in parallel
    worker_results = await asyncio.gather(
        script_and_subtitle_worker(job_request.script_text, job_request.target_languages),
        sound_stage_dubbing_worker(job_request.script_text, job_request.target_languages),
        smart_reframing_vfx_worker(job_request.video_url, job_request.target_platforms),
        global_standards_compliance_worker(job_request.script_text, job_request.target_languages)
    )
    
    fan_out_time = round(time.time() - start_time, 2)
    print(f"✅ [FAN-OUT COMPLETE] All 4 Parallel Workers finished in {fan_out_time}s!\n")
    
    # 2. FAN-IN SYNTHESIS: Pass worker outputs to Gemini for Quality Control Synthesis
    print("🧠 [FAN-IN SYNTHESIS] Gemini Showrunner Agent compiling Master Release Report...")
    synthesis_start = time.time()
    
    master_package = await synthesize_master_release_package(job_request, worker_results)
    synthesis_time = round(time.time() - synthesis_start, 2)
    
    print(f"\n🎉 [DAY 3 COMPLETE] Master Release Package Synthesized in {synthesis_time}s!")
    print(f"⏱️ Total End-to-End Pipeline Runtime: {round(time.time() - start_time, 2)}s\n")
    print("📄 --- GEMINI MASTER RELEASE REPORT ---")
    print(json.dumps(master_package["gemini_director_report"], indent=2))

if __name__ == "__main__":
    asyncio.run(run_slate_parallel())