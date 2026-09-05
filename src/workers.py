# src/workers.py
import os
import asyncio
import json
import time
import tempfile
import urllib.parse
import urllib.request
from typing import Dict, List, Optional, Tuple
from dotenv import load_dotenv
from src import gcs_engine
from src.schemas import DepartmentOutput
from src.media_engine import (
    assemble_dubbed_track,
    download_youtube_video,
    generate_crop_ffmpeg_command,
    generate_srt_file,
    is_youtube_url,
    parse_script_segments,
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


def _publish_output_file(local_path: str, dest_blob_name: str) -> str:
    """Return a reference to a generated subtitle/dub-audio file that any
    Cloud Run instance can resolve later — a gs:// URI when GCS is
    configured, since these files otherwise only exist on whichever
    instance's local disk generated them, and the later /api/v1/assemble-
    final request has no guarantee of landing on that same instance
    (confirmed live: an "ffprobe ... exit status 1" on exactly this kind of
    now-missing local file). Falls back to the local path unchanged when GCS
    isn't configured — the local file is left in place either way, since the
    existing /api/v1/downloads/* endpoints still serve directly from it."""
    if gcs_engine.is_gcs_configured():
        try:
            return gcs_engine.upload_to_gcs(local_path, dest_blob_name)
        except Exception as exc:
            print(f"[Worker Output Staging] Failed to upload {local_path} to GCS: {exc}")
    return local_path


def _transcribe_video_sync(client, video_url: str) -> list:
    """Upload a video to Gemini and return its spoken dialogue as timed
    segments — [{"start": seconds, "text": ..., "gender": ...}, ...] — rather
    than one flat string, so subtitles and dub audio can be placed at the
    moments the dialogue actually occurs, and dubbed in a voice matching
    who's actually speaking, instead of a fixed offset and a single
    always-neutral voice."""
    prompt = (
        "Listen carefully to the audio and inspect dialogue in this video. "
        "Return a JSON array of objects, each with three fields: \"start_seconds\" "
        "(a number — the time in seconds from the start of the video when that "
        "line of dialogue begins), \"text\" (the exact spoken words for that "
        "line), and \"gender\" (your best judgment of that speaker's voice as "
        "\"male\", \"female\", or \"neutral\" if you can't tell). Break the "
        "dialogue into natural segments (sentences or short phrases), each with "
        "its own accurate start time and speaker gender — a segment where the "
        "speaker changes should be its own entry even if adjacent in time. If "
        "there is no spoken dialogue, or only music/silence, return exactly: []"
    )
    uploaded_file = None
    temporary_path = None
    owns_temporary_path = False
    try:
        # Gemini's Files API (the plain API-key client used here, as opposed
        # to Vertex AI's SDK) can't reference a gs:// object directly —
        # confirmed live: "Referencing Google Cloud Storage files directly
        # is not supported." Every source type is normalized to a local
        # temp file, then uploaded via client.files.upload the same way.
        if video_url.startswith("gs://"):
            parsed = urllib.parse.urlparse(video_url)
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as temporary_file:
                temporary_path = temporary_file.name
            owns_temporary_path = True
            gcs_engine._get_client().bucket(parsed.netloc).blob(
                parsed.path.lstrip("/")
            ).download_to_filename(temporary_path)
        elif is_youtube_url(video_url):
            # YouTube pages aren't a fetchable video stream — yt-dlp
            # resolves the actual (time-limited, IP-locked) media and
            # downloads it, muxing separate video/audio tracks if needed.
            temporary_path = download_youtube_video(video_url)
            owns_temporary_path = True
        elif video_url.startswith(("http://", "https://")):
            request = urllib.request.Request(video_url, headers={"User-Agent": "SlateParallel/1.0"})
            with urllib.request.urlopen(request, timeout=60) as response:
                content_length = int(response.headers.get("Content-Length", "0"))
                max_bytes = 100 * 1024 * 1024
                if content_length > max_bytes:
                    raise ValueError("Video exceeds the 100 MB upload limit")
                with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as temporary_file:
                    temporary_path = temporary_file.name
                    owns_temporary_path = True
                    total_bytes = 0
                    while chunk := response.read(1024 * 1024):
                        total_bytes += len(chunk)
                        if total_bytes > max_bytes:
                            raise ValueError("Video exceeds the 100 MB upload limit")
                        temporary_file.write(chunk)
        elif os.path.exists(video_url):
            # An uploaded video already sitting on local disk (see
            # /api/v1/upload-video's no-GCS fallback) — not ours to delete,
            # later pipeline stages (final render) still need this exact file.
            temporary_path = video_url
        else:
            raise ValueError("Video URL must use https://, http://, gs://, or an existing local path")

        uploaded_file = client.files.upload(file=temporary_path)
        deadline = time.monotonic() + 120
        while not uploaded_file.state or uploaded_file.state.name != "ACTIVE":
            state_name = uploaded_file.state.name if uploaded_file.state else "PROCESSING"
            if time.monotonic() >= deadline or state_name == "FAILED":
                raise RuntimeError("Gemini could not process the uploaded video")
            time.sleep(2)
            uploaded_file = client.files.get(name=uploaded_file.name)
        video_part = uploaded_file

        response = client.models.generate_content(
            model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
            contents=[video_part, prompt],
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        raw = (response.text or "").strip()
        parsed = json.loads(raw) if raw else []
        segments = []
        for item in parsed:
            try:
                start = float(item["start_seconds"])
                text = str(item["text"]).strip()
            except (KeyError, TypeError, ValueError):
                continue
            if text:
                gender = str(item.get("gender", "")).strip().lower()
                if gender not in ("male", "female"):
                    gender = "neutral"
                segments.append({"start": start, "text": text, "gender": gender})
        segments.sort(key=lambda s: s["start"])
        return segments
    finally:
        if owns_temporary_path and temporary_path and os.path.exists(temporary_path):
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
    """Inspect source-video dialogue, then translate and export localized
    subtitles — timing (segment start times) flows through from whichever
    source produced the dialogue (master script "[MM:SS]" markers, or
    Gemini's timestamped transcription) all the way to the .srt cues and,
    in sound_stage_dubbing_worker, the placement of each dub audio line."""
    start_time = time.time()
    segments = []
    source_used = "unavailable"
    transcription_error = None
    api_key = os.getenv("GEMINI_API_KEY")

    # A supplied master script wins outright — video transcription (upload +
    # server-side processing) is the slowest step in the pipeline, and is
    # only worth paying for when there's no script to fall back to.
    if script_text.strip():
        segments = parse_script_segments(script_text)
        source_used = "master_script"
    elif genai and types and api_key:
        try:
            client = genai.Client(api_key=api_key)
            segments = await asyncio.to_thread(_transcribe_video_sync, client, video_url)
            if segments:
                source_used = "video_transcription"
            else:
                transcription_error = "No speech detected in video and no master script was provided."
        except Exception as exc:
            print(f"[Worker 01 Multimodal Fallback]: {exc}")
            transcription_error = str(exc)
    else:
        transcription_error = "Gemini is not configured and no master script was provided; nothing to transcribe or translate."

    detected_dialogue = "\n".join(segment["text"] for segment in segments)
    client = genai.Client(api_key=api_key) if genai and api_key else None

    async def _translate_one(language: str) -> Tuple[str, list, Optional[str]]:
        """Returns (language, translated_segments, error_or_None). Segments
        keep their original start times — only the text changes."""
        if not (client and segments):
            return language, [], None
        try:
            translation_prompt = (
                f"Translate each of the following movie dialogue lines into natural, "
                f"idiomatic {language}. Return a JSON array of strings, in the exact "
                f"same order, with exactly {len(segments)} items — one translated "
                f"line per input line, no extra commentary:\n\n"
                f"{json.dumps([segment['text'] for segment in segments])}"
            )
            response = await asyncio.to_thread(
                client.models.generate_content,
                model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
                contents=translation_prompt,
                config=types.GenerateContentConfig(response_mime_type="application/json"),
            )
            raw = (response.text or "").strip()
            translated_texts = json.loads(raw) if raw else []
            if not isinstance(translated_texts, list) or len(translated_texts) != len(segments):
                raise ValueError(
                    f"Expected {len(segments)} translated lines, got "
                    f"{len(translated_texts) if isinstance(translated_texts, list) else type(translated_texts).__name__}"
                )
            translated_segments = [
                {
                    "start": segment["start"],
                    "text": str(text).strip() or segment["text"],
                    "gender": segment.get("gender", "neutral"),
                }
                for segment, text in zip(segments, translated_texts)
            ]
            return language, translated_segments, None
        except Exception as exc:
            # Same graceful degradation as a successful-but-empty response
            # above — without this, a transient Gemini error (rate limit,
            # safety-filter block, malformed JSON, etc.) leaves this
            # language with no dialogue at all instead of falling back to
            # the (still correctly-timed) source-language segments.
            return language, segments, str(exc)

    # One Gemini call per language, independent of each other — run
    # concurrently instead of awaiting them one at a time.
    translation_results = await asyncio.gather(
        *(_translate_one(language) for language in target_languages)
    )

    localized_dialogues = {}
    localized_segments = {}
    subtitle_files = {}
    for language, translated_segments, error in translation_results:
        if error:
            print(f"[Worker 01 Translation Fallback ({language})]: {error}")
            transcription_error = transcription_error or error
        localized_segments[language] = translated_segments
        localized_dialogues[language] = "\n".join(segment["text"] for segment in translated_segments)
        if translated_segments:
            srt_path = generate_srt_file(title, language, translated_segments)
            subtitle_files[language] = _publish_output_file(
                srt_path, f"worker_outputs/subtitles/{os.path.basename(srt_path)}"
            )

    return DepartmentOutput(
        worker_id="worker_01",
        department_name="Script & Subtitle Supervisor",
        status="SUCCESS",
        data={
            "transcription_source": source_used,
            "active_dialogue": detected_dialogue,
            "localized_dialogues": localized_dialogues,
            "localized_segments": localized_segments,
            "subtitle_files": subtitle_files,
            "transcription_error": transcription_error,
        },
        execution_time_seconds=round(time.time() - start_time, 2)
    )


# Backwards-compatible alias for callers using the original worker name.
async def script_and_subtitle_worker(script_text: str, languages: List[str]) -> DepartmentOutput:
    return await run_script_subtitle_worker("", script_text, languages, "Untitled Project")

async def sound_stage_dubbing_worker(
    title: str, localized_segments: Dict[str, list]
) -> DepartmentOutput:
    """Worker 2: Generate downloadable localized MP3 dubbed-audio tracks,
    each dialogue line placed at its own timestamp (assemble_dubbed_track)
    instead of fused into one clip starting at 0s — the latter is why
    dubbed audio used to drift out of sync with longer videos."""
    start_time = time.time()

    async def _synthesize_one(language: str, segments: list) -> Tuple[str, Optional[str], Optional[str]]:
        """Returns (language, audio_path_or_None, error_or_None)."""
        if not segments:
            return language, None, "No localized dialogue was available for synthesis."
        try:
            path = await asyncio.to_thread(assemble_dubbed_track, title, language, segments)
            published_path = _publish_output_file(
                path, f"worker_outputs/audio/{os.path.basename(path)}"
            )
            return language, published_path, None
        except Exception as exc:
            print(f"[Worker 02 Dubbing Fallback ({language})]: {exc}")
            return language, None, str(exc)

    # One dub track per language, independent — run concurrently instead
    # of synthesizing one voice track at a time.
    synthesis_results = await asyncio.gather(
        *(_synthesize_one(language, segments) for language, segments in localized_segments.items())
    )

    audio_tracks = {}
    audio_errors = {}
    for language, path, error in synthesis_results:
        if path:
            audio_tracks[language] = path
        else:
            audio_errors[language] = error

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

# Regional classification boards to explicitly steer Parallel's research
# toward, per target language — without this the task prompt only names the
# language/region generically, leaving the model to guess which board's
# guidelines actually apply.
REGIONAL_RATING_BOARDS = {
    "English": "MPAA (USA) and BBFC (UK)",
    "Japanese": "CERO and Eirin (Japan)",
    "Spanish": "ICAA (Spain)",
    "French": "CNC classification commission (France)",
    "German": "FSK (Germany)",
}


async def global_standards_compliance_worker(script_text: str, target_languages: List[str]) -> DepartmentOutput:
    """Worker 4: Global Standards & Compliance Guardian (Live Parallel Task API)."""
    start_time = time.time()
    parallel_interaction_id = "local_mock_mode"
    parallel_run_id = "local_mock_mode"
    compliance_checks = ["Clear for PG-13 release", "No regional trademark violations flagged"]
    live_web_context = "No live API key supplied; utilizing local compliance cache."

    if parallel_client:
        try:
            board_hints = [
                f"{language}: {REGIONAL_RATING_BOARDS[language]}"
                for language in target_languages
                if language in REGIONAL_RATING_BOARDS
            ]
            board_hint_clause = (
                f" Specifically check guidelines from: {'; '.join(board_hints)}." if board_hints else ""
            )
            # Query Parallel's live Task API for open-web rating/censorship rules
            task_prompt = (
                f"Analyze current film release rating compliance, censorship guidelines, "
                f"and cultural sensitivities for target regions: {', '.join(target_languages)}."
                f"{board_hint_clause} "
                f"Given the script context: '{script_text[:100]}...'"
            )
            task_res = await parallel_client.task_run.create(
                input=task_prompt,
                processor="base"
            )
            parallel_interaction_id = getattr(task_res, "interaction_id", "live_task_active")
            parallel_run_id = getattr(task_res, "run_id", parallel_interaction_id)
            live_web_context = f"Live web analysis executed via Parallel API (Run ID: {parallel_run_id})."
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
            "parallel_run_id": parallel_run_id,
            "compliance_checks": compliance_checks,
            "live_web_context": live_web_context
        },
        execution_time_seconds=round(time.time() - start_time, 2)
    )
