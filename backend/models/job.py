"""Versioned public contract for durable background jobs."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


JOB_SCHEMA_NAME = "geotile_job"
JOB_SCHEMA_VERSION = 1
JOB_STATE_SCHEMA_NAME = "geotile_job_state"
JOB_STATE_SCHEMA_VERSION = 1


class JobType(str, Enum):
    TRAINING = "training"
    DATASET_BUILD = "dataset_build"
    SCENE_IMPORT = "scene_import"
    SCENE_SCAN = "scene_scan"
    SCENE_PREPARATION = "scene_preparation"
    # Osobny typ, a nie wariant SCENE_PREPARATION: tamten payload wymaga dokladnie trzech
    # pasm RGB i uruchamia pansharpening, wiec nie da sie go uzyc do budowy
    # pelnorozdzielczego COG z jednopasmowego JP2 (JP2_FULL_RESOLUTION_DECISION_PLAN, R1.2).
    SCENE_FULLRES_DERIVATIVE = "scene_fullres_derivative"
    SCENE_OVERVIEW = "scene_overview"
    SCENE_INFERENCE = "scene_inference"
    EMBEDDING_ANALYSIS = "embedding_analysis"
    DATASET_EXPORT = "dataset_export"
    PROJECT_BACKUP = "project_backup"
    ARTIFACT_CLEANUP = "artifact_cleanup"
    # Pelne sha256 zasobow pomiarowych przed eksportem paczki adnotacji. Osobne zadanie,
    # bo czyta cale rastry zrodlowe (w praktyce gigabajty) — jako zwykly request
    # blokowaloby interfejs i nie dalo sie go przerwac.
    ANNOTATION_PACKAGE_PREPARE = "annotation_package_prepare"


class ResourceClass(str, Enum):
    GPU_EXCLUSIVE = "gpu_exclusive"
    CPU_HEAVY = "cpu_heavy"
    IO_HEAVY = "io_heavy"
    # Lightweight directory/manifest discovery.  Keeping it separate prevents a
    # long raster overview build from starving project-creation scans.
    IO_METADATA = "io_metadata"


class PriorityClass(str, Enum):
    INTERACTIVE = "interactive"
    USER_BACKGROUND = "user_background"
    MAINTENANCE = "maintenance"


PRIORITY_VALUES: dict[PriorityClass, int] = {
    PriorityClass.INTERACTIVE: 300,
    PriorityClass.USER_BACKGROUND: 200,
    PriorityClass.MAINTENANCE: 100,
}


class JobStatus(str, Enum):
    QUEUED = "queued"
    STARTING = "starting"
    RUNNING = "running"
    CANCELLING = "cancelling"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


TERMINAL_JOB_STATUSES = {
    JobStatus.COMPLETED.value,
    JobStatus.CANCELLED.value,
    JobStatus.FAILED.value,
    JobStatus.INTERRUPTED.value,
}
ACTIVE_JOB_STATUSES = {
    JobStatus.QUEUED.value,
    JobStatus.STARTING.value,
    JobStatus.RUNNING.value,
    JobStatus.CANCELLING.value,
}


class JobCreateRequest(BaseModel):
    """Generic API request. Domain endpoints use the same fields internally."""

    model_config = ConfigDict(extra="forbid")

    job_type: JobType
    resource_class: ResourceClass
    priority_class: PriorityClass = PriorityClass.USER_BACKGROUND
    payload: dict[str, Any] = Field(default_factory=dict)
    dedupe_key: str | None = Field(default=None, max_length=256)


class JobSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_name: str = JOB_SCHEMA_NAME
    schema_version: int = JOB_SCHEMA_VERSION
    job_id: str
    job_type: JobType
    project_id: str
    resource_class: ResourceClass
    priority_class: PriorityClass
    priority: int
    payload: dict[str, Any] = Field(default_factory=dict)
    dedupe_key: str | None = None
    retry_of: str | None = None
    attempt: int = 1
    created_at: str


class JobState(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_name: str = JOB_STATE_SCHEMA_NAME
    schema_version: int = JOB_STATE_SCHEMA_VERSION
    job_id: str
    job_type: JobType
    project_id: str
    resource_class: ResourceClass
    priority: int
    status: JobStatus = JobStatus.QUEUED
    phase: str = "queued"
    current: int | float | None = None
    total: int | float | None = None
    process: dict[str, Any] | None = None
    heartbeat: str | None = None
    cancel_requested: bool = False
    error: str | None = None
    artifacts: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    started_at: str | None = None
    ended_at: str | None = None
    updated_at: str
    revision: int = 1
    event_seq: int = 0
