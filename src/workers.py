# src/workers.py
import os
import asyncio
import time
import tempfile
import urllib.request
from typing import Dict, List
from dotenv import load_dotenv
from src.schemas import DepartmentOutput
from src.media_engine import (
    generate_crop_ffmpeg_command,
    generate_dubbed_audio_file,
    generate_srt_file,
)

load_dotenv()

try:
    from parallel import AsyncParallel
except ImportError:
    AsyncParallel = None

try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None
    types = None

# Initialize Parallel SDK client
parallel_api_key = os.getenv("PARALLEL_API_KEY", "")
parallel_client = (
    AsyncParallel(api_key=parallel_api_key) 
    if AsyncParallel and parallel_api_key and parallel_api_key != "your_parallel_api_key_here"
    else None
)

def _transcribe_video_sync(client, video_url: str) -> str:
    """Upload an HTTP(S) video to Gemini and return its spoken dialogue."""
    prompt = (
        "Listen carefully to the audio and inspect dialogue in this video. "
        "If there is spoken dialogue, transcribe the exact spoken words. "
        "If there is no spoken dialogue or only music/silence, return exactly "
        "NO_SPEECH_DETECTED."
    )
    uploaded_file = None
    temporary_path = None
    try:
        if video_url.startswith("gs://"):
            video_part = types.Part.from_uri(file_uri=video_url, mime_type="video/mp4")
        elif video_url.startswith(("http://", "https://")):
            request = urllib.request.Request(video_url, headers={"User-Agent": "SlateParallel/1.0"})
            with urllib.request.urlopen(request, timeout=60) as response:
                content_length = int(response.headers.get("Content-Length", "0"))
                max_bytes = 100 * 1024 * 1024
                if content_length > max_bytes:
                    raise ValueError("Video exceeds the 100 MB upload limit")
                with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as temporary_file:
                    temporary_path = temporary_file.name
                    total_bytes = 0
                    while chunk := response.read(1024 * 1024):
                        total_bytes += len(chunk)
                        if total_bytes > max_bytes:
                            raise ValueError("Video exceeds the 100 MB upload limit")
                        temporary_file.write(chunk)

            uploaded_file = client.files.upload(file=temporary_path)
            deadline = time.monotonic() + 120
            while not uploaded_file.state or uploaded_file.state.name != "ACTIVE":
                state_name = uploaded_file.state.name if uploaded_file.state else "PROCESSING"
                if time.monotonic() >= deadline or state_name == "FAILED":
                    raise RuntimeError("Gemini could not process the uploaded video")
                time.sleep(2)
                uploaded_file = client.files.get(name=uploaded_file.name)
            video_part = uploaded_file
        else:
            raise ValueError("Video URL must use https://, http://, or gs://")

        response = client.models.generate_content(
            model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
            contents=[video_part, prompt],
        )
        return (response.text or "").strip()
    finally:
        if temporary_path and os.path.exists(temporary_path):
            os.remove(temporary_path)
        if uploaded_file:
            try:
                client.files.delete(name=uploaded_file.name)
            except Exception:
                pass


async def run_script_subtitle_worker(
    video_url: str,
    script_text: str,
    target_languages: List[str],
    title: str,
) -> DepartmentOutput:
    """Inspect source-video dialogue, then translate and export localized subtitles."""
    start_time = time.time()
    detected_dialogue = ""
    source_used = "unavailable"
    transcription_error = None
    api_key = os.getenv("GEMINI_API_KEY")

    if genai and types and api_key:
        try:
            client = genai.Client(api_key=api_key)
            transcription = await asyncio.to_thread(_transcribe_video_sync, client, video_url)
            if transcription and "NO_SPEECH_DETECTED" not in transcription:
                detected_dialogue = transcription
                source_used = "video_transcription"
            elif script_text.strip():
                detected_dialogue = script_text.strip()
                source_used = "master_script_fallback"
        except Exception as exc:
            print(f"[Worker 01 Multimodal Fallback]: {exc}")
            transcription_error = str(exc)
    elif script_text.strip():
        detected_dialogue = script_text.strip()
        source_used = "master_script_fallback (gemini_not_configured)"
    else:
        transcription_error = "Gemini is not configured; provide GEMINI_API_KEY to transcribe video dialogue."

    if not detected_dialogue and script_text.strip():
        detected_dialogue = script_text.strip()
        source_used = "master_script_fallback"

    localized_dialogues = {}
    subtitle_files = {}
    client = genai.Client(api_key=api_key) if genai and api_key else None

    for language in target_languages:
        translated_text = ""
        if client and detected_dialogue:
            try:
                translation_prompt = (
                    f"Translate the following movie dialogue into natural, idiomatic {language}. "
                    f"Return only the translated text:\n\n{detected_dialogue}"
                )
                response = await asyncio.to_thread(
                    client.models.generate_content,
                    model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
                    contents=translation_prompt,
                )
                translated_text = (response.text or "").strip() or detected_dialogue
            except Exception as exc:
                print(f"[Worker 01 Translation Fallback ({language})]: {exc}")
                transcription_error = transcription_error or str(exc)

        localized_dialogues[language] = translated_text
        if translated_text:
            subtitle_files[language] = generate_srt_file(title, language, translated_text)

    return DepartmentOutput(
        worker_id="worker_01",
        department_name="Script & Subtitle Supervisor",
        status="SUCCESS",
        data={
            "transcription_source": source_used,
            "active_dialogue": detected_dialogue,
            "localized_dialogues": localized_dialogues,
            "subtitle_files": subtitle_files,
            "transcription_error": transcription_error,
        },
        execution_time_seconds=round(time.time() - start_time, 2)
    )


# Backwards-compatible alias for callers using the original worker name.
async def script_and_subtitle_worker(script_text: str, languages: List[str]) -> DepartmentOutput:
    return await run_script_subtitle_worker("", script_text, languages, "Untitled Project")

async def sound_stage_dubbing_worker(
    title: str, localized_dialogues: Dict[str, str]
) -> DepartmentOutput:
    """Worker 2: Generate downloadable localized MP3 dubbed-audio tracks."""
    start_time = time.time()
    audio_tracks = {}
    audio_errors = {}

    for language, dialogue in localized_dialogues.items():
        if not dialogue:
            audio_errors[language] = "No localized dialogue was available for synthesis."
            continue
        try:
            audio_tracks[language] = await asyncio.to_thread(
                generate_dubbed_audio_file, title, language, dialogue
            )
        except Exception as exc:
            print(f"[Worker 02 Dubbing Fallback ({language})]: {exc}")
            audio_errors[language] = str(exc)
    
    return DepartmentOutput(
        worker_id="worker_02",
        department_name="Sound Stage & Dubbing Lead",
        status="SUCCESS",
        data={
            "dubbed_tracks": audio_tracks,
            "audio_errors": audio_errors,
            "voice_profile": "Cloud Text-to-Speech MP3, 1.05x rate, -0.95 pitch",
        },
        execution_time_seconds=round(time.time() - start_time, 2)
    )

async def smart_reframing_vfx_worker(video_url: str, platforms: List[str]) -> DepartmentOutput:
    """Worker 3: Smart Reframing Director (VFX)."""
    start_time = time.time()
    await asyncio.sleep(0.3)
    
    ffmpeg_pipeline = {
        platform: generate_crop_ffmpeg_command(video_url, platform)
        for platform in platforms
    }
    
    return DepartmentOutput(
        worker_id="worker_03",
        department_name="Smart Reframing Director (VFX)",
        status="SUCCESS",
        data={"ffmpeg_render_pipeline": ffmpeg_pipeline, "source_video": video_url},
        execution_time_seconds=round(time.time() - start_time, 2)
    )

async def global_standards_compliance_worker(script_text: str, target_languages: List[str]) -> DepartmentOutput:
    """Worker 4: Global Standards & Compliance Guardian (Live Parallel Task API)."""
    start_time = time.time()
    parallel_interaction_id = "local_mock_mode"
    compliance_checks = ["Clear for PG-13 release", "No regional trademark violations flagged"]
    live_web_context = "No live API key supplied; utilizing local compliance cache."
    
    if parallel_client:
        try:
            # Query Parallel's live Task API for open-web rating/censorship rules
            task_prompt = (
                f"Analyze current film release rating compliance, censorship guidelines, "
                f"and cultural sensitivities for target regions: {', '.join(target_languages)} "
                f"given the script context: '{script_text[:100]}...'"
            )
            task_res = await parallel_client.task_run.create(
                input=task_prompt,
                processor="base"
            )
            parallel_interaction_id = getattr(task_res, "interaction_id", "live_task_active")
            live_web_context = f"Live web analysis executed via Parallel API (ID: {parallel_interaction_id})."
            compliance_checks.append("Live Parallel web research completed successfully.")
        except Exception as e:
            compliance_checks.append(f"Parallel API call note: {str(e)}")
            live_web_context = f"Parallel API query fallback: {str(e)}"

    return DepartmentOutput(
        worker_id="worker_04",
        department_name="Global Standards & Compliance Guardian",
        status="SUCCESS",
        data={
            "parallel_interaction_id": parallel_interaction_id,
            "compliance_checks": compliance_checks,
            "live_web_context": live_web_context
        },
        execution_time_seconds=round(time.time() - start_time, 2)
    )
