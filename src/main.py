# src/main.py
import asyncio
import time
from dotenv import load_dotenv
from src.schemas import MediaJobRequest
from src.workers import (
    script_and_subtitle_worker,
    sound_stage_dubbing_worker,
    smart_reframing_vfx_worker,
    global_standards_compliance_worker
)

load_dotenv()

async def run_slate_parallel():
    print("🎬 [SHOWRUNNER AGENT] Initializing 'slate-parallel' Orchestrator Pipeline...\n")
    
    # Sample Producer Request Payload
    job_request = MediaJobRequest(
        title="Inception 2 Teaser",
        script_text="Protagonist: We have to go deeper into the subconscious layer.",
        video_url="https://storage.googleapis.com/sample-bucket/raw_trailer.mp4",
        target_languages=["Japanese", "Spanish"],
        target_platforms=["TikTok (9:16)", "Instagram (1:1)"]
    )
    
    start_time = time.time()
    print(f"⚡ FAN-OUT: Dispatching 4 Department Workers simultaneously for '{job_request.title}'...")
    
    # Trigger all 4 workers concurrently at the exact same millisecond
    results = await asyncio.gather(
        script_and_subtitle_worker(job_request.script_text, job_request.target_languages),
        sound_stage_dubbing_worker(job_request.script_text, job_request.target_languages),
        smart_reframing_vfx_worker(job_request.video_url, job_request.target_platforms),
        global_standards_compliance_worker(job_request.script_text, job_request.target_languages)
    )
    
    total_time = round(time.time() - start_time, 2)
    print(f"\n✅ FAN-IN: All 4 Parallel Workers Completed in {total_time}s!\n")
    
    for worker_res in results:
        print(f"  • [{worker_res.department_name}] Status: {worker_res.status} | Time: {worker_res.execution_time_seconds}s")
        print(f"    Data: {worker_res.data}\n")

if __name__ == "__main__":
    asyncio.run(run_slate_parallel())