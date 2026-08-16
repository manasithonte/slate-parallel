# src/media_engine.py
import os
from pathlib import Path
from typing import Dict

OUTPUT_DIR = Path("outputs")

def ensure_output_dir():
    """Creates the local outputs/ directory if it doesn't already exist."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

def export_srt_subtitles(title: str, subtitles: Dict[str, str]) -> Dict[str, str]:
    """
    Writes translated subtitle content into standard timecoded .srt files.
    Saves outputs to the local outputs/ directory.
    """
    ensure_output_dir()
    saved_files = {}
    clean_title = title.lower().replace(" ", "_")
    
    for lang, srt_text in subtitles.items():
        file_name = f"{clean_title}_{lang.lower()}.srt"
        file_path = OUTPUT_DIR / file_name
        
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(f"1\n00:00:01,000 --> 00:00:04,000\n[{lang} Title] {title}\n\n")
            f.write(f"2\n00:00:04,100 --> 00:00:08,000\n{srt_text}\n")
            
        saved_files[lang] = str(file_path)
        
    return saved_files

def generate_crop_ffmpeg_command(input_video_path: str, aspect_ratio: str) -> str:
    """
    Generates dynamic FFmpeg crop filter commands based on target platform aspect ratios:
    - 9:16 Vertical (TikTok/Shorts): crop=ih*(9/16):ih
    - 1:1 Square (Instagram): crop=ih:ih
    """
    if "9:16" in aspect_ratio:
        crop_filter = "crop=ih*(9/16):ih:(iw-ih*(9/16))/2:0"
        output_tag = "9x16_vertical"
    elif "1:1" in aspect_ratio:
        crop_filter = "crop=ih:ih:(iw-ih)/2:0"
        output_tag = "1x1_square"
    else:
        crop_filter = "copy"
        output_tag = "16x9_master"
        
    output_filename = f"outputs/trailer_{output_tag}.mp4"
    return f"ffmpeg -i {input_video_path} -vf '{crop_filter}' -c:a copy {output_filename}"