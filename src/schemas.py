# src/schemas.py
from pydantic import BaseModel, Field
from typing import List, Dict, Any

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
