# src/gcs_engine.py
"""Google Cloud Storage helpers for the final-assembly (Transcoder) stage.

Transcoder API only reads/writes via gs:// URIs, so source video, dubbed
audio, and subtitle files must be staged in a bucket before a render job
can reference them. Kept separate from media_engine.py (local file
synthesis) since this is a distinct, independently-removable concern.
"""
import datetime
import os
import tempfile
import urllib.request
from dotenv import load_dotenv

try:
    from google.cloud import storage
except ImportError:
    storage = None

from src.media_engine import download_youtube_video, is_youtube_url

load_dotenv()

GCS_BUCKET_NAME = os.getenv("GCS_BUCKET_NAME", "")

_storage_client = None


def is_gcs_configured() -> bool:
    """True only if the SDK is installed and a bucket name is set."""
    return bool(storage and GCS_BUCKET_NAME)


def _get_client():
    global _storage_client
    if _storage_client is None:
        _storage_client = storage.Client()
    return _storage_client


def upload_to_gcs(local_path: str, dest_blob_name: str) -> str:
    """Upload a local file to the configured bucket, returning its gs:// URI."""
    if not is_gcs_configured():
        raise RuntimeError("GCS is not configured (missing SDK or GCS_BUCKET_NAME)")
    bucket = _get_client().bucket(GCS_BUCKET_NAME)
    blob = bucket.blob(dest_blob_name)
    blob.upload_from_filename(local_path)
    return f"gs://{GCS_BUCKET_NAME}/{dest_blob_name}"


def upload_source_video(video_url_or_path: str, render_job_id: str) -> str:
    """Stage the source video in GCS, handling gs://, local path, YouTube, and http(s) inputs."""
    if video_url_or_path.startswith("gs://"):
        return video_url_or_path

    if is_youtube_url(video_url_or_path):
        temporary_path = download_youtube_video(video_url_or_path)
        try:
            return upload_to_gcs(
                temporary_path, f"renders/{render_job_id}/source.mp4"
            )
        finally:
            if os.path.exists(temporary_path):
                os.remove(temporary_path)

    if video_url_or_path.startswith(("http://", "https://")):
        request = urllib.request.Request(
            video_url_or_path, headers={"User-Agent": "SlateParallel/1.0"}
        )
        temporary_path = None
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                content_length = int(response.headers.get("Content-Length", "0"))
                max_bytes = 100 * 1024 * 1024
                if content_length > max_bytes:
                    raise ValueError("Video exceeds the 100 MB upload limit")
                with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as temp_file:
                    temporary_path = temp_file.name
                    total_bytes = 0
                    while chunk := response.read(1024 * 1024):
                        total_bytes += len(chunk)
                        if total_bytes > max_bytes:
                            raise ValueError("Video exceeds the 100 MB upload limit")
                        temp_file.write(chunk)
            return upload_to_gcs(
                temporary_path, f"renders/{render_job_id}/source.mp4"
            )
        finally:
            if temporary_path and os.path.exists(temporary_path):
                os.remove(temporary_path)

    if os.path.exists(video_url_or_path):
        return upload_to_gcs(
            video_url_or_path, f"renders/{render_job_id}/source.mp4"
        )

    raise ValueError("video_url must be gs://, http(s)://, or an existing local path")


def generate_signed_download_url(gcs_uri: str, expiration_minutes: int = 60) -> str:
    """Generate a time-limited signed URL for a browser to download directly from GCS."""
    if not is_gcs_configured():
        raise RuntimeError("GCS is not configured (missing SDK or GCS_BUCKET_NAME)")
    if not gcs_uri.startswith(f"gs://{GCS_BUCKET_NAME}/"):
        raise ValueError("gcs_uri is not within the configured bucket")
    blob_name = gcs_uri[len(f"gs://{GCS_BUCKET_NAME}/"):]
    bucket = _get_client().bucket(GCS_BUCKET_NAME)
    blob = bucket.blob(blob_name)
    return blob.generate_signed_url(
        version="v4",
        expiration=datetime.timedelta(minutes=expiration_minutes),
        method="GET",
    )
