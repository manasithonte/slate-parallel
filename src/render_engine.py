# src/render_engine.py
"""Final-assembly stage: combine crop + dub audio + subtitles into one .mp4
per platform via Google Cloud Transcoder API, using whichever dub-audio and
subtitle language was chosen for that platform (RenderJobRequest.platform_renditions).

This stage is additive and independent of the core /api/v1/process-media
flow — if it doesn't pan out, delete this file, gcs_engine.py, the two
new endpoints in api.py, and the schema additions with zero impact on
the rest of the pipeline.

Every external call here follows the same optional-dependency pattern as
genai/vertexai/parallel/texttospeech elsewhere in this codebase: missing
SDK or missing config degrades to a NOT_CONFIGURED status, never a crash.
"""
import asyncio
import datetime
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
import uuid
from typing import Dict, Optional, Tuple
from dotenv import load_dotenv

from src import gcs_engine
from src.media_engine import crop_and_burn_subtitles, download_youtube_video, get_crop_parameters, is_youtube_url, pad_audio_to_duration
from src.schemas import RenderJob, RenderJobRequest, RenderJobStatus, RenderOutput

try:
    from google.cloud.video import transcoder_v1
except ImportError:
    transcoder_v1 = None

try:
    from google.cloud import firestore
except ImportError:
    firestore = None

load_dotenv()

TRANSCODER_LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
TRANSCODER_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "")
POLL_INTERVAL_SECONDS = 10
MAX_POLL_ATTEMPTS = 180  # ~30 minutes per job
RENDER_JOBS_COLLECTION = "render_jobs"

# Per-instance fast path — still authoritative for whichever Cloud Run
# instance is actually running a given job's background task, and the only
# store available at all in local dev without Firestore configured. Mirrored
# to Firestore below because Cloud Run gives no guarantee that a later
# GET /render-status poll lands on this same instance.
RENDER_JOBS: Dict[str, RenderJob] = {}

_transcoder_client = None
_firestore_client = None


def is_transcoder_configured() -> bool:
    return bool(transcoder_v1 and TRANSCODER_PROJECT and gcs_engine.is_gcs_configured())


def _get_client():
    global _transcoder_client
    if _transcoder_client is None:
        _transcoder_client = transcoder_v1.TranscoderServiceClient()
    return _transcoder_client


def _get_firestore_client():
    global _firestore_client
    if _firestore_client is None and firestore and TRANSCODER_PROJECT:
        _firestore_client = firestore.Client(project=TRANSCODER_PROJECT)
    return _firestore_client


def _save_render_job_sync(render_job: RenderJob) -> None:
    client = _get_firestore_client()
    if not client:
        return
    client.collection(RENDER_JOBS_COLLECTION).document(render_job.render_job_id).set(
        render_job.model_dump(mode="json")
    )


async def _persist(render_job: RenderJob) -> None:
    """Mirror the current render job state to Firestore. Best-effort: a
    persistence failure is logged, not fatal — the in-memory copy in the
    instance actually doing the work is still correct for it, and status
    polls from that same instance keep working regardless."""
    try:
        await asyncio.to_thread(_save_render_job_sync, render_job)
    except Exception as exc:
        print(f"[Render Job Persistence] Failed to save {render_job.render_job_id}: {exc}")


async def get_render_job(render_job_id: str) -> Optional[RenderJob]:
    """Look up a render job for a status poll — same-instance memory first
    (fast path; the only option in local dev with no Firestore configured),
    falling back to Firestore for a job whose background task is running on
    a different Cloud Run instance than the one serving this request."""
    if render_job_id in RENDER_JOBS:
        return RENDER_JOBS[render_job_id]
    client = _get_firestore_client()
    if not client:
        return None
    try:
        doc = await asyncio.to_thread(
            lambda: client.collection(RENDER_JOBS_COLLECTION).document(render_job_id).get()
        )
    except Exception as exc:
        # Same best-effort spirit as _persist's write side — Firestore being
        # unreachable/misconfigured should surface as "job not found" (a
        # normal 404 the frontend already handles), not a 500 that crashes
        # the whole status-poll endpoint.
        print(f"[Render Job Persistence] Failed to read {render_job_id}: {exc}")
        return None
    if doc.exists:
        return RenderJob(**doc.to_dict())
    return None


def _crop_pixels_for_platform(platform: str, width: int, height: int) -> "transcoder_v1.types.PreprocessingConfig.Crop":
    """Translate the same aspect-ratio rules used for the ffmpeg crop filter
    (see get_crop_parameters in media_engine.py) into exact pixel offsets —
    used only when Transcoder itself performs the crop, i.e. no subtitles
    are being burned in for this platform so no local ffmpeg pass ran."""
    crop = get_crop_parameters(platform)
    if crop["aspect_ratio"] == "9:16":
        target_width = int(height * 9 / 16)
        side = max(0, (width - target_width) // 2)
        return transcoder_v1.types.PreprocessingConfig.Crop(left_pixels=side, right_pixels=side)
    if crop["aspect_ratio"] == "1:1":
        side = max(0, (width - height) // 2)
        return transcoder_v1.types.PreprocessingConfig.Crop(left_pixels=side, right_pixels=side)
    return transcoder_v1.types.PreprocessingConfig.Crop()


def _ensure_local_file(path_or_uri: str, suffix: str) -> Tuple[str, bool]:
    """Resolve a subtitle/audio reference to a local path ffmpeg/ffprobe can
    read. worker_01/worker_02 now upload their outputs straight to GCS when
    it's configured (so any Cloud Run instance can reach them, not just the
    one that generated them) — this downloads that gs:// object to a local
    temp file on demand. A plain local path (GCS not configured, single-
    process dev) passes through unchanged. Returns (local_path, owns_it) —
    owns_it is True only for a freshly-downloaded temp file the caller must
    delete; False for a path someone else is responsible for."""
    if path_or_uri.startswith("gs://"):
        parsed = urllib.parse.urlparse(path_or_uri)
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temp_file:
            local_path = temp_file.name
        gcs_engine._get_client().bucket(parsed.netloc).blob(
            parsed.path.lstrip("/")
        ).download_to_filename(local_path)
        return local_path, True
    return path_or_uri, False


def _probe_video_dimensions(local_path: str) -> Tuple[int, int, float]:
    """Read source width/height/duration via ffprobe. Width/height feed
    Transcoder's own crop for platforms with no subtitles to burn (see
    _crop_pixels_for_platform); duration always drives the EditAtom trim
    below regardless of which crop path a platform takes."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height:format=duration", "-of", "json", local_path,
        ],
        capture_output=True, text=True, timeout=30, check=True,
    )
    data = json.loads(result.stdout)
    stream = data["streams"][0]
    duration = float(data["format"]["duration"])
    return int(stream["width"]), int(stream["height"]), duration


def _probe_audio_duration(local_path: str) -> float:
    """Read a local audio file's duration via ffprobe, in seconds."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "json", local_path,
        ],
        capture_output=True, text=True, timeout=30, check=True,
    )
    return float(json.loads(result.stdout)["format"]["duration"])


def _download_source_to_local(video_url: str) -> str:
    """Fetch the source video (gs://, http(s)://, or an existing local path)
    to a local temp file so ffmpeg can crop/scale/burn subtitles into it per
    platform — Transcoder can't read local paths, and this must happen
    before the per-platform upload. Caller is responsible for deleting the
    result."""
    if is_youtube_url(video_url):
        return download_youtube_video(video_url)

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as temp_file:
        local_path = temp_file.name

    if video_url.startswith("gs://"):
        parsed = urllib.parse.urlparse(video_url)
        bucket = gcs_engine._get_client().bucket(parsed.netloc)
        bucket.blob(parsed.path.lstrip("/")).download_to_filename(local_path)
        return local_path

    if video_url.startswith(("http://", "https://")):
        request = urllib.request.Request(video_url, headers={"User-Agent": "SlateParallel/1.0"})
        max_bytes = 100 * 1024 * 1024
        with urllib.request.urlopen(request, timeout=60) as response:
            content_length = int(response.headers.get("Content-Length", "0"))
            if content_length > max_bytes:
                raise ValueError("Video exceeds the 100 MB upload limit")
            total_bytes = 0
            with open(local_path, "wb") as out:
                while chunk := response.read(1024 * 1024):
                    total_bytes += len(chunk)
                    if total_bytes > max_bytes:
                        raise ValueError("Video exceeds the 100 MB upload limit")
                    out.write(chunk)
        return local_path

    if os.path.exists(video_url):
        shutil.copyfile(video_url, local_path)
        return local_path

    os.remove(local_path)
    raise ValueError("video_url must be gs://, http(s)://, or an existing local path")


def _build_job(
    render_job_id: str,
    platform: str,
    dub_language: Optional[str],
    subtitle_language: Optional[str],
    video_gcs_uri: str,
    video_duration: float,
    audio_gcs_uri: Optional[str],
    audio_duration: Optional[float],
    raw_video_dims: Optional[Tuple[int, int]] = None,
) -> "transcoder_v1.types.Job":
    crop = get_crop_parameters(platform)
    out_width, out_height = crop["output_width"], crop["output_height"]
    dub_tag = dub_language.lower() if dub_language else "nodub"
    sub_tag = subtitle_language.lower() if subtitle_language else "nosubs"
    output_filename = f"{crop['output_tag']}_dub-{dub_tag}_subs-{sub_tag}.mp4"

    video_input_kwargs = {"key": "video-input", "uri": video_gcs_uri}
    if raw_video_dims is not None:
        # No subtitles to burn for this platform, so no local ffmpeg pass
        # ran — video_gcs_uri is still the raw, un-cropped source. Let
        # Transcoder do the crop itself here rather than paying for a
        # redundant local transcode just to hand it an already-cropped file.
        width, height = raw_video_dims
        video_input_kwargs["preprocessing_config"] = transcoder_v1.types.PreprocessingConfig(
            crop=_crop_pixels_for_platform(platform, width, height)
        )
    # Otherwise video_gcs_uri already points at a locally cropped/scaled/
    # subtitled file (see _run_render_pipeline), so no crop is needed here —
    # this job only re-encodes and mux in the dub track.
    inputs = [transcoder_v1.types.Input(**video_input_kwargs)]
    atom_inputs = ["video-input"]

    audio_stream_kwargs = {"codec": "aac", "bitrate_bps": 128000}
    if audio_gcs_uri:
        inputs.append(transcoder_v1.types.Input(key="dub-audio-input", uri=audio_gcs_uri))
        atom_inputs.append("dub-audio-input")
        # Cloud TTS dub tracks (media_engine.generate_dubbed_audio_file) are
        # mono. channel_count must be declared explicitly, or Transcoder
        # defaults to stereo (2 channels) and rejects a mapping that only
        # covers output_channel 0. channel_layout must independently match
        # channel_count's length (it does not auto-derive from it) — "fc"
        # (front center) is the mono layout, vs. the stereo default ["fl","fr"].
        audio_stream_kwargs["channel_count"] = 1
        audio_stream_kwargs["channel_layout"] = ["fc"]
        audio_stream_kwargs["mapping_"] = [
            transcoder_v1.types.AudioStream.AudioMapping(
                atom_key="atom0", input_key="dub-audio-input",
                input_track=0, input_channel=0, output_channel=0,
            )
        ]

    elementary_streams = [
        transcoder_v1.types.ElementaryStream(
            key="video-stream0",
            video_stream=transcoder_v1.types.VideoStream(
                h264=transcoder_v1.types.VideoStream.H264CodecSettings(
                    height_pixels=out_height, width_pixels=out_width,
                    bitrate_bps=3500000, frame_rate=30,
                )
            ),
        ),
        transcoder_v1.types.ElementaryStream(
            key="audio-stream0",
            audio_stream=transcoder_v1.types.AudioStream(**audio_stream_kwargs),
        ),
    ]
    # NOTE: Transcoder API confirmed (via a live 400 response — "expect no
    # standalone text streams in a ts/mp4 mux stream") that a TextStream
    # cannot be embedded into a plain mp4 MuxStream; captions only work in
    # HLS/DASH manifest outputs, which this stage doesn't produce. Instead,
    # subtitles are hard-burned into the video's pixels — after crop/scale,
    # so caption size/position match the final frame — before this job ever
    # runs (see crop_and_burn_subtitles in media_engine.py, invoked per
    # platform in _run_render_pipeline).
    mux_elementary_streams = ["video-stream0", "audio-stream0"]

    # Transcoder derives an atom's implicit duration from its inputs and
    # rejects an end_time_offset (or, left unset, an implicit one) that
    # exceeds any single input's own length — e.g. a translated dub track
    # is rarely the exact same length as the source video. Trim explicitly
    # to the shortest of the two (a small safety margin avoids float
    # rounding landing exactly on — or a hair past — the true minimum).
    edit_atom_kwargs = {"key": "atom0", "inputs": atom_inputs}
    if audio_duration is not None:
        shortest_seconds = max(0.1, min(video_duration, audio_duration) - 0.05)
        edit_atom_kwargs["end_time_offset"] = datetime.timedelta(seconds=shortest_seconds)

    job = transcoder_v1.types.Job()
    job.output_uri = f"gs://{gcs_engine.GCS_BUCKET_NAME}/renders/{render_job_id}/"
    job.config = transcoder_v1.types.JobConfig(
        inputs=inputs,
        edit_list=[transcoder_v1.types.EditAtom(**edit_atom_kwargs)],
        elementary_streams=elementary_streams,
        mux_streams=[
            transcoder_v1.types.MuxStream(
                key="mp4-output", container="mp4",
                file_name=output_filename, elementary_streams=mux_elementary_streams,
            )
        ],
    )
    return job, output_filename


async def _submit_and_poll(
    render_job_id: str, index: int, platform: str,
    dub_language: Optional[str], subtitle_language: Optional[str],
    video_gcs_uri: str, video_duration: float,
    audio_gcs_uri: Optional[str], audio_duration: Optional[float],
    raw_video_dims: Optional[Tuple[int, int]] = None,
):
    render_job = RENDER_JOBS[render_job_id]
    output = render_job.outputs[index]
    try:
        job, output_filename = _build_job(
            render_job_id, platform, dub_language, subtitle_language,
            video_gcs_uri, video_duration, audio_gcs_uri, audio_duration,
            raw_video_dims,
        )
        client = _get_client()
        parent = f"projects/{TRANSCODER_PROJECT}/locations/{TRANSCODER_LOCATION}"
        created = await asyncio.to_thread(client.create_job, parent=parent, job=job)
        output.transcoder_job_name = created.name
        output.status = RenderJobStatus.SUBMITTED
        await _persist(render_job)

        for _ in range(MAX_POLL_ATTEMPTS):
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
            polled = await asyncio.to_thread(client.get_job, name=created.name)
            state = polled.state.name if polled.state else "PENDING"
            if state == "SUCCEEDED":
                output_uri = job.output_uri + output_filename
                output.output_gcs_uri = output_uri
                output.status = RenderJobStatus.SUCCEEDED
                try:
                    output.download_url = await asyncio.to_thread(
                        gcs_engine.generate_signed_download_url, output_uri
                    )
                except Exception as exc:
                    output.error = f"Rendered but signed URL failed: {exc}"
                await _persist(render_job)
                return
            if state == "FAILED":
                output.status = RenderJobStatus.FAILED
                output.error = polled.error.message if polled.error else "Transcoder job failed"
                await _persist(render_job)
                return

        output.status = RenderJobStatus.FAILED
        output.error = f"Timed out after {MAX_POLL_ATTEMPTS * POLL_INTERVAL_SECONDS}s waiting on Transcoder"
        await _persist(render_job)
    except Exception as exc:
        output.status = RenderJobStatus.FAILED
        output.error = str(exc)
        await _persist(render_job)


async def _run_render_pipeline(render_job_id: str, request: RenderJobRequest):
    render_job = RENDER_JOBS[render_job_id]
    try:
        local_source_path = await asyncio.to_thread(_download_source_to_local, request.video_url)
        width, height, video_duration = await asyncio.to_thread(_probe_video_dimensions, local_source_path)
    except Exception as exc:
        for output in render_job.outputs:
            output.status = RenderJobStatus.FAILED
            output.error = f"Source video staging failed: {exc}"
        render_job.overall_status = RenderJobStatus.FAILED
        await _persist(render_job)
        return

    platform_video_uris: Dict[str, str] = {}
    platform_needs_transcoder_crop: Dict[str, bool] = {}
    dub_audio_uris: Dict[str, Optional[str]] = {}
    dub_audio_durations: Dict[str, Optional[float]] = {}
    rendered_local_paths = []

    # Shared raw-source upload for platforms with no subtitles to burn —
    # uploaded at most once and reused, guarded against a race between
    # concurrently-running platforms both wanting it at the same time.
    raw_source_gcs_uri: Optional[str] = None
    raw_source_lock = asyncio.Lock()

    async def _get_raw_source_uri() -> str:
        nonlocal raw_source_gcs_uri
        async with raw_source_lock:
            if raw_source_gcs_uri is None:
                raw_source_gcs_uri = await asyncio.to_thread(
                    gcs_engine.upload_to_gcs, local_source_path,
                    f"renders/{render_job_id}/video_source_raw.mp4",
                )
        return raw_source_gcs_uri

    async def _stage_platform_video(platform: str, rendition) -> None:
        subtitle_language = rendition.subtitle_language
        srt_ref = request.subtitle_files.get(subtitle_language) if subtitle_language else None
        srt_path = None
        if srt_ref:
            try:
                local_srt_path, owns_srt = await asyncio.to_thread(_ensure_local_file, srt_ref, ".srt")
                if owns_srt:
                    rendered_local_paths.append(local_srt_path)
                if os.path.exists(local_srt_path):
                    srt_path = local_srt_path
            except Exception:
                # Same graceful degradation as before for a stale/missing
                # local path: render without subtitles for this platform
                # rather than failing the whole job over one missing file.
                srt_path = None

        if not srt_path:
            # No subtitles to burn — skip the local ffmpeg crop/scale pass
            # entirely (it would just be a redundant transcode ahead of
            # Transcoder's own) and let Transcoder crop the raw source.
            platform_needs_transcoder_crop[platform] = True
            platform_video_uris[platform] = await _get_raw_source_uri()
            return

        platform_needs_transcoder_crop[platform] = False
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as rendered_file:
            rendered_path = rendered_file.name
        # Track for cleanup immediately, before the risky ffmpeg/upload work,
        # so a sibling platform's failure (which cancels this task mid-flight
        # under asyncio.gather) doesn't leak this temp file.
        rendered_local_paths.append(rendered_path)
        await asyncio.to_thread(
            crop_and_burn_subtitles, local_source_path, platform, srt_path, rendered_path
        )
        crop_tag = get_crop_parameters(platform)["output_tag"]
        platform_video_uris[platform] = await asyncio.to_thread(
            gcs_engine.upload_to_gcs, rendered_path,
            f"renders/{render_job_id}/video_{crop_tag}_{(subtitle_language or 'nosubs').lower()}.mp4",
        )

    async def _stage_dub_audio(dub_language: str) -> None:
        audio_ref = request.dubbed_tracks.get(dub_language)
        if not audio_ref:
            dub_audio_uris[dub_language] = None
            dub_audio_durations[dub_language] = None
            return
        audio_path, owns_audio_path = await asyncio.to_thread(_ensure_local_file, audio_ref, ".mp3")
        if owns_audio_path:
            rendered_local_paths.append(audio_path)
        audio_duration = await asyncio.to_thread(_probe_audio_duration, audio_path)
        if audio_duration < video_duration:
            # A dub track shorter than the source video used to force the
            # EditAtom below to trim to the shorter of the two — cutting the
            # video itself short to match. Padding the audio with trailing
            # silence up to the video's own length means the atom can always
            # be trimmed to the full video length instead (see _build_job).
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as padded_file:
                padded_path = padded_file.name
            rendered_local_paths.append(padded_path)
            await asyncio.to_thread(pad_audio_to_duration, audio_path, video_duration, padded_path)
            audio_path = padded_path
            audio_duration = video_duration
        dub_audio_durations[dub_language] = audio_duration
        dub_audio_uris[dub_language] = await asyncio.to_thread(
            gcs_engine.upload_to_gcs, audio_path,
            f"renders/{render_job_id}/audio_{dub_language.lower()}.mp3"
        )

    # Each platform's crop/subtitle-burn/upload, and each unique dub
    # language's probe/upload, are fully independent — run them all
    # concurrently instead of one platform at a time before any Transcoder
    # job is even submitted.
    unique_dub_languages = {
        rendition.dub_language for rendition in request.platform_renditions.values() if rendition.dub_language
    }
    try:
        await asyncio.gather(
            *(_stage_platform_video(platform, rendition) for platform, rendition in request.platform_renditions.items()),
            *(_stage_dub_audio(language) for language in unique_dub_languages),
        )
    except Exception as exc:
        for output in render_job.outputs:
            output.status = RenderJobStatus.FAILED
            output.error = f"Crop/subtitle-burn or staging failed: {exc}"
        render_job.overall_status = RenderJobStatus.FAILED
        await _persist(render_job)
        return
    finally:
        for path in [local_source_path] + rendered_local_paths:
            if path and os.path.exists(path):
                os.remove(path)

    tasks = []
    for index, (platform, rendition) in enumerate(request.platform_renditions.items()):
        dub_language = rendition.dub_language
        audio_gcs_uri = dub_audio_uris.get(dub_language) if dub_language else None
        audio_duration = dub_audio_durations.get(dub_language) if dub_language else None
        raw_video_dims = (width, height) if platform_needs_transcoder_crop.get(platform) else None
        tasks.append(_submit_and_poll(
            render_job_id, index, platform, dub_language, rendition.subtitle_language,
            platform_video_uris[platform], video_duration, audio_gcs_uri, audio_duration,
            raw_video_dims,
        ))

    await asyncio.gather(*tasks)

    statuses = {o.status for o in render_job.outputs}
    if RenderJobStatus.SUCCEEDED in statuses:
        render_job.overall_status = RenderJobStatus.SUCCEEDED  # partial success reflected per-output
    else:
        render_job.overall_status = RenderJobStatus.FAILED
    await _persist(render_job)


async def submit_render_job(request: RenderJobRequest) -> RenderJob:
    """Fire-and-poll: returns as soon as the render job is registered and
    background rendering has been scheduled, not when rendering finishes."""
    render_job_id = uuid.uuid4().hex
    outputs = [
        RenderOutput(
            platform=platform,
            dub_language=rendition.dub_language,
            subtitle_language=rendition.subtitle_language,
            status=RenderJobStatus.PENDING,
        )
        for platform, rendition in request.platform_renditions.items()
    ]
    render_job = RenderJob(
        render_job_id=render_job_id,
        overall_status=RenderJobStatus.PENDING,
        outputs=outputs,
        created_at=time.time(),
    )
    RENDER_JOBS[render_job_id] = render_job

    if not is_transcoder_configured():
        message = "Transcoder API is not configured (missing SDK, GOOGLE_CLOUD_PROJECT, or GCS_BUCKET_NAME)"
        for output in render_job.outputs:
            output.status = RenderJobStatus.NOT_CONFIGURED
            output.error = message
        render_job.overall_status = RenderJobStatus.NOT_CONFIGURED
        await _persist(render_job)
        return render_job

    render_job.overall_status = RenderJobStatus.SUBMITTED
    await _persist(render_job)
    asyncio.create_task(_run_render_pipeline(render_job_id, request))
    return render_job
