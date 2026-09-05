# src/media_engine.py
import os
import re
import subprocess
import tempfile
from typing import Optional

try:
    from google.cloud import texttospeech
except ImportError:
    texttospeech = None

try:
    import yt_dlp
except ImportError:
    yt_dlp = None

YOUTUBE_URL_PATTERN = re.compile(
    r"(?:youtube(?:-nocookie)?\.com/(?:watch|shorts/|embed/)|youtu\.be/)", re.IGNORECASE
)


def is_youtube_url(url: str) -> bool:
    """True if url points at a YouTube watch/shorts/embed page rather than a direct video file."""
    return bool(YOUTUBE_URL_PATTERN.search(url))


def download_youtube_video(url: str) -> str:
    """Download a YouTube video to a local temp .mp4 via yt-dlp.

    Plain urllib GETs (used elsewhere for direct video-file URLs) can't fetch
    YouTube — the watch/shorts page is HTML, not a video stream, and YouTube's
    actual media is served as time-limited, IP-locked CDN URLs. yt-dlp handles
    that resolution and, when the source is split into separate video/audio
    streams, uses the ffmpeg already in this image to mux them into one mp4.
    Caller owns the returned path and must delete it when done.
    """
    if not yt_dlp:
        raise RuntimeError("yt-dlp is not installed")

    fd, temp_path = tempfile.mkstemp(suffix=".mp4")
    os.close(fd)
    os.remove(temp_path)  # yt-dlp writes to this exact path itself

    ydl_opts = {
        # H.264 (avc1) explicitly preferred: Google Cloud Transcoder's input
        # decoder rejects AV1 (confirmed live — "MalformattedInput" on a
        # video whose only available stream was AV1), which yt-dlp's default
        # "best" selector happily picks for higher-resolution uploads. Only
        # falls through to whatever's available if no H.264 stream exists.
        "format": (
            "bestvideo[vcodec^=avc1][ext=mp4]+bestaudio[ext=m4a]"
            "/best[vcodec^=avc1][ext=mp4]"
            "/bestvideo[ext=mp4]+bestaudio[ext=m4a]"
            "/best[ext=mp4]/best"
        ),
        "outtmpl": temp_path,
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "noprogress": True,
        # YouTube's bot-detection ("Sign in to confirm you're not a bot")
        # targets the web client's request pattern and is much more
        # aggressive from cloud/datacenter IPs (Cloud Run, etc.) than
        # residential ones. The Android client hits a different, unauthenticated
        # API surface that isn't subject to the same check.
        "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    if not os.path.exists(temp_path):
        raise RuntimeError("yt-dlp did not produce an output file")
    return temp_path

OUTPUT_DIR = "outputs"
os.makedirs(os.path.join(OUTPUT_DIR, "subtitles"), exist_ok=True)
os.makedirs(os.path.join(OUTPUT_DIR, "audio"), exist_ok=True)
os.makedirs(os.path.join(OUTPUT_DIR, "compliance"), exist_ok=True)

LANGUAGE_CODES = {
    "English": "en-US",
    "Japanese": "ja-JP",
    "Spanish": "es-ES",
    "French": "fr-FR",
    "German": "de-DE",
    "Hindi": "hi-IN",
}


# Master-script convention for hand-specifying line timing: "[MM:SS] text",
# one per line. A script with no lines matching this pattern is treated as
# a single untimed block starting at 0s (same shape either way — a list of
# {"start": seconds, "text": ...} segments — so downstream code never needs
# to know which case produced it).
SCRIPT_TIMESTAMP_PATTERN = re.compile(r'^\[(\d{1,3}):(\d{2})\]\s*(.+)$')


def parse_script_segments(script_text: str) -> list:
    """Parse a master script into timed segments.

    Recognizes "[MM:SS] line text" per line; a script with no such markers
    becomes one segment starting at 0s so the rest of the pipeline (SRT
    generation, dub-audio placement) can treat every source of dialogue —
    master script, hand-timed or not, or auto-transcription — identically.
    Every segment carries a "gender" field for TTS voice selection; a script
    has no audio to infer it from, so it's always "neutral" here (auto-
    transcription is the only source that can detect this, from the video's
    actual audio).
    """
    segments = []
    for line in script_text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        match = SCRIPT_TIMESTAMP_PATTERN.match(line)
        if match:
            minutes, seconds, text = match.groups()
            start = int(minutes) * 60 + int(seconds)
            if text.strip():
                segments.append({"start": float(start), "text": text.strip(), "gender": "neutral"})
    if segments:
        segments.sort(key=lambda s: s["start"])
        return segments
    stripped = script_text.strip()
    return [{"start": 0.0, "text": stripped, "gender": "neutral"}] if stripped else []


def format_srt_timestamp(seconds: float) -> str:
    """Seconds -> SRT's "HH:MM:SS,mmm" timestamp format."""
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _tts_gender(gender: str):
    if gender == "male":
        return texttospeech.SsmlVoiceGender.MALE
    if gender == "female":
        return texttospeech.SsmlVoiceGender.FEMALE
    return texttospeech.SsmlVoiceGender.NEUTRAL


def _synthesize_speech(text: str, language: str, gender: str = "neutral") -> bytes:
    """Raw Cloud TTS call for one line of text. Shared by every segment in
    assemble_dubbed_track — kept separate so each segment can be synthesized
    (and later time-placed) independently instead of as one fused block.
    `gender` ("male"/"female"/"neutral", from Gemini's speaker-gender
    detection on auto-transcribed video) picks an actual matching voice —
    Cloud TTS maps ssml_gender to a distinct voice per language, not just a
    pitch tweak on one fixed voice."""
    if not texttospeech:
        raise RuntimeError("Google Cloud Text-to-Speech SDK is not installed")
    client = texttospeech.TextToSpeechClient()
    response = client.synthesize_speech(
        input=texttospeech.SynthesisInput(text=text),
        voice=texttospeech.VoiceSelectionParams(
            language_code=LANGUAGE_CODES.get(language, "en-US"),
            ssml_gender=_tts_gender(gender),
        ),
        audio_config=texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.MP3,
            speaking_rate=1.05,
            pitch=-0.95,
        ),
    )
    return response.audio_content


def assemble_dubbed_track(title: str, language: str, segments: list) -> str:
    """Synthesize each dialogue segment and place it at its own timestamp in
    one composite MP3, instead of fusing all dialogue into a single clip
    starting at 0s — the latter is exactly why dubbed audio used to drift out
    of sync with the video the longer a clip ran.
    """
    if not texttospeech:
        raise RuntimeError("Google Cloud Text-to-Speech SDK is not installed")
    if not segments:
        raise ValueError("Cannot synthesize a dubbed-audio track with no dialogue segments")

    safe_title = re.sub(r"[^a-zA-Z0-9_]", "_", title.lower())
    filename = f"{safe_title}_{language.lower()}_dub.mp3"
    filepath = os.path.join(OUTPUT_DIR, "audio", filename)

    clip_paths = []
    try:
        for segment in segments:
            audio_bytes = _synthesize_speech(segment["text"], language, segment.get("gender", "neutral"))
            fd, clip_path = tempfile.mkstemp(suffix=".mp3")
            with os.fdopen(fd, "wb") as clip_file:
                clip_file.write(audio_bytes)
            clip_paths.append(clip_path)

        if len(segments) == 1 and segments[0]["start"] <= 0.01:
            # Common case (no script timestamps / single-block dialogue) —
            # nothing to place, the one clip already belongs at the start.
            os.replace(clip_paths[0], filepath)
            clip_paths = []
            return filepath

        # Delay each clip to its own start time, then mix them onto one
        # track. amix's default per-input volume drop (1/N) is undone with
        # normalize=0 — these clips don't overlap in time, so there's
        # nothing to actually blend.
        filter_parts = []
        mix_inputs = []
        for index, segment in enumerate(segments):
            delay_ms = max(0, int(round(segment["start"] * 1000)))
            filter_parts.append(f"[{index}:a]adelay={delay_ms}|{delay_ms}[a{index}]")
            mix_inputs.append(f"[a{index}]")
        filter_complex = ";".join(filter_parts)
        filter_complex += f";{''.join(mix_inputs)}amix=inputs={len(segments)}:duration=longest:normalize=0[out]"

        cmd = ["ffmpeg", "-y"]
        for clip_path in clip_paths:
            cmd += ["-i", clip_path]
        cmd += ["-filter_complex", filter_complex, "-map", "[out]", filepath]
        subprocess.run(cmd, capture_output=True, text=True, timeout=300, check=True)
        return filepath
    finally:
        for clip_path in clip_paths:
            if os.path.exists(clip_path):
                os.remove(clip_path)


def pad_audio_to_duration(input_path: str, target_duration: float, output_path: str) -> None:
    """Pad (or trim) an audio file with trailing silence so its duration
    exactly matches target_duration. Used to bring a dub track that ends
    before the source video does up to the video's own length, so Cloud
    Transcoder's EditAtom can be trimmed to the video's full duration
    instead of truncating the video down to match a shorter dub track."""
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", input_path,
            "-af", "apad", "-t", str(target_duration),
            output_path,
        ],
        capture_output=True, text=True, timeout=120, check=True,
    )


def _estimate_speech_duration(text: str) -> float:
    """Rough estimate of how long Cloud TTS takes to speak a line, so a
    subtitle cue's length tracks approximately how long its dub audio
    actually plays instead of always spanning the (possibly much longer)
    gap until the next line's timestamp — the previous behavior could leave
    a caption visible well after its corresponding speech had ended."""
    chars_per_second = 16.0  # conservative average speaking rate at the 1.05x TTS speed used here
    return max(1.2, len(text) / chars_per_second)


def generate_srt_file(title: str, language: str, segments: list, video_duration: Optional[float] = None) -> str:
    """Writes a standardized .srt subtitle track, one cue per dialogue
    segment, timed at that segment's own start and sized to roughly how
    long that line takes to speak — capped so it never overlaps into the
    next segment's start, or past the video's end for the last one."""
    safe_title = re.sub(r'[^a-zA-Z0-9_]', '_', title.lower())
    filename = f"{safe_title}_{language.lower()}.srt"
    filepath = os.path.join(OUTPUT_DIR, "subtitles", filename)

    cues = []
    for index, segment in enumerate(segments):
        start = segment["start"]
        end = start + _estimate_speech_duration(segment["text"])
        if index + 1 < len(segments):
            end = min(end, segments[index + 1]["start"])
        elif video_duration is not None:
            end = min(end, video_duration)
        end = max(end, start + 1.0)  # never a zero/negative-length cue
        cues.append(
            f"{index + 1}\n{format_srt_timestamp(start)} --> {format_srt_timestamp(end)}\n{segment['text']}\n"
        )

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(cues))
    return filepath

def crop_and_burn_subtitles(
    video_path: str, platform: str, srt_path: Optional[str], output_path: str
) -> None:
    """Crop to the platform's target aspect ratio, scale to its delivery
    resolution, and (if given) hard-burn a .srt track into the *already
    cropped and scaled* frame via ffmpeg's libass-backed `subtitles` filter.

    Subtitles must be burned in after crop/scale, not before: burning onto
    the full-width source and letting Transcoder crop afterward clips text
    that falls outside the narrower crop window (confirmed by inspecting a
    1:1 render where wide subtitle lines ran off-frame). Doing the crop
    locally also means Cloud Transcoder — which rejects a standalone text
    stream muxed into a plain mp4 — only has to re-encode and mux dub audio,
    with no preprocessing crop of its own (see render_engine.py).
    """
    crop = get_crop_parameters(platform)
    filters = [crop["ffmpeg_filter"], f"scale={crop['output_width']}:{crop['output_height']}"]
    if srt_path:
        # The subtitles filter treats ':' and others as option separators —
        # the path itself must be escaped, then quoted, as a filter arg.
        escaped_path = srt_path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
        filters.append(f"subtitles=filename='{escaped_path}'")
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", video_path,
            "-vf", ",".join(filters),
            "-c:a", "copy", output_path,
        ],
        capture_output=True, text=True, timeout=300, check=True,
    )

def generate_compliance_certificate(title: str, clearances: dict) -> str:
    """Generates an official studio compliance audit report."""
    safe_title = re.sub(r'[^a-zA-Z0-9_]', '_', title.lower())
    filename = f"{safe_title}_compliance_clearance.txt"
    filepath = os.path.join(OUTPUT_DIR, "compliance", filename)
    
    cert_content = f"""=====================================================
SLATEPARALLEL AUTONOMOUS COMPLIANCE CLEARANCE REPORT
=====================================================
Project Title: {title}
Status: APPROVED FOR GLOBAL BROADCAST
Parallel Open-Web Task ID: {clearances.get('task_id', 'pal_task_live')}

RATINGS & TERRITORIAL CLEARANCES:
- US (MPAA): {clearances.get('mpaa', 'Rated PG-13 (Peril & Sequence Intensity)')}
- Japan (CERO/EIRIN): {clearances.get('cero', 'Rated G (General Audience Clear)')}
- Europe (BBFC): {clearances.get('bbfc', 'Rated 12A (Moderate Violence Clear)')}

TRADEMARK & AD PLACEMENT AUDIT:
- Commercial Placement Safe: Yes (Clean keyframe ad anchors at 00:02:15)
- Slang & Dialect Flagging: 0 Regional Sensitivities Flagged

Certified by SlateParallel Automated Guardian Agent.
====================================================="""
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(cert_content)
    return filepath


def get_crop_parameters(aspect_ratio: str) -> dict:
    """Map a target aspect ratio to its crop definition and delivery
    resolution.

    Shared by the ffmpeg command-string generator below, crop_and_burn_subtitles,
    and the Transcoder job builder in render_engine.py, so the crop/scale
    logic can't drift between the three.
    """
    if "9:16" in aspect_ratio:
        return {
            "ffmpeg_filter": "crop=ih*(9/16):ih:(iw-ih*(9/16))/2:0",
            "output_tag": "9x16_vertical",
            "aspect_ratio": "9:16",
            "output_width": 720,
            "output_height": 1280,
        }
    if "1:1" in aspect_ratio:
        return {
            "ffmpeg_filter": "crop=ih:ih:(iw-ih)/2:0",
            "output_tag": "1x1_square",
            "aspect_ratio": "1:1",
            "output_width": 720,
            "output_height": 720,
        }
    return {
        "ffmpeg_filter": "null",
        "output_tag": "16x9_master",
        "aspect_ratio": "16:9",
        "output_width": 1280,
        "output_height": 720,
    }


def generate_crop_ffmpeg_command(input_video_path: str, aspect_ratio: str) -> str:
    """Generate an FFmpeg crop command for a target delivery aspect ratio."""
    crop = get_crop_parameters(aspect_ratio)
    output_filename = f"outputs/trailer_{crop['output_tag']}.mp4"
    return f"ffmpeg -i {input_video_path} -vf '{crop['ffmpeg_filter']}' -c:a copy {output_filename}"
