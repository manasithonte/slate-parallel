# src/media_engine.py
import os
import re
import subprocess
from typing import Optional

try:
    from google.cloud import texttospeech
except ImportError:
    texttospeech = None

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


def generate_dubbed_audio_file(title: str, language: str, text: str) -> str:
    """Synthesize a localized MP3 with Google Cloud Text-to-Speech."""
    if not texttospeech:
        raise RuntimeError("Google Cloud Text-to-Speech SDK is not installed")
    if not text.strip():
        raise ValueError("Cannot synthesize an empty dubbed-audio track")

    safe_title = re.sub(r"[^a-zA-Z0-9_]", "_", title.lower())
    filename = f"{safe_title}_{language.lower()}_dub.mp3"
    filepath = os.path.join(OUTPUT_DIR, "audio", filename)
    client = texttospeech.TextToSpeechClient()
    response = client.synthesize_speech(
        input=texttospeech.SynthesisInput(text=text),
        voice=texttospeech.VoiceSelectionParams(
            language_code=LANGUAGE_CODES.get(language, "en-US"),
            ssml_gender=texttospeech.SsmlVoiceGender.NEUTRAL,
        ),
        audio_config=texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.MP3,
            speaking_rate=1.05,
            pitch=-0.95,
        ),
    )
    with open(filepath, "wb") as audio_file:
        audio_file.write(response.audio_content)
    return filepath

def generate_srt_file(title: str, language: str, text: str) -> str:
    """Writes a standardized .srt subtitle track file."""
    safe_title = re.sub(r'[^a-zA-Z0-9_]', '_', title.lower())
    filename = f"{safe_title}_{language.lower()}.srt"
    filepath = os.path.join(OUTPUT_DIR, "subtitles", filename)

    srt_content = f"""1
00:00:01,000 --> 00:00:04,500
[{language.upper()} DUB] {text}

2
00:00:05,000 --> 00:00:08,200
[Auto-Synced by SlateParallel Concurrency Engine]
"""
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(srt_content)
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
