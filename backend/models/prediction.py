from typing import Literal

from pydantic import BaseModel, Field, field_validator
from datetime import datetime, timezone


class PredictionConfig(BaseModel):
    model_path: str = ""
    conf: float = Field(default=0.25, ge=0.01, le=1.0)
    iou: float = Field(default=0.4, ge=0.1, le=1.0)
    tile_size: int = Field(default=640, ge=128, le=2048)
    buffer: int = Field(default=64, ge=0)
    device: str = "cpu"
    img_size: int | None = None
    # Whole-scene inference pipeline. ``auto`` is conservative on CPU and uses
    # available VRAM tiers on CUDA; an integer is an explicit, reproducible batch.
    batch_size: Literal["auto"] | int = "auto"
    prefetch_batches: int = Field(default=2, ge=1, le=8)
    merge_method: Literal["nms"] = "nms"
    progress_interval_ms: int = Field(default=250, ge=50, le=5000)
    preprocess_mode: Literal["auto", "linear", "log"] = "auto"
    stretch_low: float = Field(default=2.0, ge=0.0, le=100.0)
    stretch_high: float = Field(default=98.0, ge=0.0, le=100.0)
    gamma: float = Field(default=1.0, gt=0.0, le=5.0)
    brightness: float = Field(default=1.0, ge=0.0, le=5.0)
    contrast: float = Field(default=1.0, ge=0.0, le=5.0)
    # SAM click-to-box checkpoint (EO and SAR); None uses no SAM backend.
    sam_checkpoint: str | None = None
    # Optional user-selected directory containing SAM checkpoints. The path is
    # kept per project and may point to an internal, external or network disk.
    sam_models_dir: str | None = None
    # SAR exemplar backbone weights (SARATR-X/FG-MAE, GPU); None disables the SAR
    # exemplar tool unless the mock backend is on.
    sar_exemplar_backbone: str | None = None
    # Domyślny silnik narzędzia „Znajdź podobne": klasyczny szablon (NCC) albo few-shot DINO.
    exemplar_engine: Literal["template", "dino"] = "template"
    # Wagi DINO dla silnika egzemplarza (i analizy): katalog + wybrany checkpoint.
    # None → domyślny katalog MODELS_ROOT/dino i auto-wybór wariantu.
    dino_models_dir: str | None = None
    dino_checkpoint: str | None = None

    @field_validator("batch_size")
    @classmethod
    def validate_batch_size(cls, value: Literal["auto"] | int):
        if value == "auto":
            return value
        if isinstance(value, bool) or not 1 <= int(value) <= 64:
            raise ValueError("batch_size must be 'auto' or an integer from 1 to 64")
        return int(value)


class Prediction(BaseModel):
    id: str
    class_id: int = -1
    class_name: str = ""
    bbox: list[float]  # [x_min, y_min, x_max, y_max] scene pixels
    confidence: float
    source_model: str = ""
    status: str = "pending"  # "pending" | "accepted" | "rejected"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
