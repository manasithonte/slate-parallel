# src/schemas.py
from enum import Enum
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

class MediaJobRequest(BaseModel):
    """Input payload submitted by the studio producer."""
    title: str = Field(..., description="Project title or movie trailer name")
    script_text: str = Field(
        default="",
        description="Optional master script used only when source-video dialogue is unavailable",
    )
    video_url: str = Field(..., description="URL or local path to raw master footage")
    target_languages: List[str] = Field(
        default_factory=lambda: ["Japanese", "Spanish", "French"],
        description="Target localization languages"
    )
    target_platforms: List[str] = Field(
        default_factory=lambda: ["YouTube (16:9)", "TikTok (9:16)", "Instagram (1:1)"],
        description="Aspect ratios and platforms for re-framing"
    )

class DepartmentOutput(BaseModel):
    """Output schema returned by each specialized worker agent."""
    worker_id: str
    department_name: str
    status: str  # "SUCCESS" or "FAILED"
    data: Dict[str, Any]
    execution_time_seconds: float


class RenderJobStatus(str, Enum):
    """Lifecycle states for a final-assembly Transcoder render job."""
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    NOT_CONFIGURED = "NOT_CONFIGURED"


class RenderOutput(BaseModel):
    """Status of one platform x language combination in a render job."""
    platform: str
    language: str
    status: RenderJobStatus
    transcoder_job_name: Optional[str] = None
    output_gcs_uri: Optional[str] = None
    download_url: Optional[str] = None
    error: Optional[str] = None


class RenderJobRequest(BaseModel):
    """Input payload for the final-assembly (Transcoder) stage.

    Client resends the relevant worker_01/worker_02 outputs since
    /api/v1/process-media is stateless and caches nothing server-side.
    """
    title: str
    video_url: str
    target_languages: List[str]
    target_platforms: List[str]
    localized_dialogues: Dict[str, str] = Field(default_factory=dict)
    subtitle_files: Dict[str, str] = Field(default_factory=dict)
    dubbed_tracks: Dict[str, str] = Field(default_factory=dict)


class RenderJob(BaseModel):
    """Top-level tracked state for a submitted final-assembly job."""
    render_job_id: str
    overall_status: RenderJobStatus
    outputs: List[RenderOutput]
    created_at: float
