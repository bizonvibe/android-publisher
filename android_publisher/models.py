from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator

Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.-]{1,100}$")]


class Mode(StrEnum):
    TRIAL_REEL = "TRIAL_REEL"
    NORMAL_REEL = "NORMAL_REEL"
    SHARE_TRIAL_TO_EVERYONE = "SHARE_TRIAL_TO_EVERYONE"


class Execution(StrEnum):
    DRY_RUN = "DRY_RUN"
    LIVE = "LIVE"


class Job(BaseModel):
    video_id: Identifier
    r2_object_key: str = Field(min_length=1, max_length=1024)
    account_id: Identifier  # Instagram username, not Graph API numeric ID
    caption: str = Field(default="", max_length=2200)
    publication_mode: Mode
    scheduled_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    idempotency_key: str = Field(min_length=1, max_length=200)
    execution_mode: Execution = Execution.DRY_RUN
    source_job_id: str | None = None

    @field_validator("scheduled_at")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None:
            raise ValueError("scheduled_at requires timezone")
        return value.astimezone(UTC)

    @field_validator("r2_object_key")
    @classmethod
    def key(cls, value):
        if not value.endswith(".mp4") or value.startswith("/") or ".." in value.split("/"):
            raise ValueError("Expected relative MP4 object key")
        return value

    @model_validator(mode="after")
    def source(self):
        if (self.publication_mode == Mode.SHARE_TRIAL_TO_EVERYONE) != bool(self.source_job_id):
            raise ValueError("source_job_id is required only for SHARE_TRIAL_TO_EVERYONE")
        return self


class Manifest(BaseModel):
    video_id: Identifier
    object_key: str
    sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    size_bytes: int = Field(gt=0, le=1_073_741_824)


class Stop(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class Retryable(Exception):
    """Only safe before the durable submission barrier."""
