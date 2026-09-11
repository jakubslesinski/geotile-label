"""Models used by package-based scene import."""

from typing import Literal

from pydantic import BaseModel, Field

from models.project import ProjectProfile


SceneProvider = Literal[
    "iceye",
    "capella",
    "umbra",
    "pleiades_neo",
    "worldview",
    "blacksky",
    "generic",
]

SceneImportMode = Literal["on_demand", "background", "prepare_all"]


class SceneSourceCreate(BaseModel):
    provider: SceneProvider
    root_path: str
    enabled: bool = True


class SceneSource(SceneSourceCreate):
    source_id: str
    added_at: str
    last_scan_at: str | None = None
    last_scan_status: str | None = None


class SceneImportPreviewRequest(BaseModel):
    sources: list[SceneSourceCreate] = Field(min_length=1)
    modality: Literal["SAR", "EO"] | None = None


class SceneSelectionDecision(BaseModel):
    package_id: str
    # Stable only within a concrete source.  ``package_id`` describes the delivery
    # structure and may legitimately repeat when two roots contain the same product.
    decision_uid: str | None = None
    action: Literal["import", "skip"] = "import"
    asset_ids: list[str] = Field(default_factory=list)
    rgb_bands: list[int] | None = None


class ProjectFromSourcesCreate(BaseModel):
    name: str
    preview_id: str
    sources: list[SceneSourceCreate] = Field(min_length=1)
    decisions: list[SceneSelectionDecision] = Field(default_factory=list)
    classes_file: str | None = None
    project_location: str | None = None
    profile: ProjectProfile | None = None
    # Wstępne parametry siatki kafli (można zmienić później w Siatce przeglądu).
    tile_size: int = Field(640, ge=128, le=4096)
    buffer: int = Field(0, ge=0)
    import_mode: SceneImportMode = "background"


class SceneSourcesApplyRequest(BaseModel):
    preview_id: str
    decisions: list[SceneSelectionDecision] = Field(default_factory=list)
    import_mode: SceneImportMode | None = None


class SceneImportResumeRequest(BaseModel):
    import_mode: SceneImportMode | None = None


class SceneImportConfigRequest(BaseModel):
    # Pola sa opcjonalne, bo widok zapisuje niezalezne ustawienia tego samego
    # dokumentu. Backend scala zmiane z aktualna konfiguracja projektu.
    import_mode: SceneImportMode | None = None
    auto_fullres_cog_enabled: bool | None = None


class SceneSourceRelinkRequest(BaseModel):
    root_path: str
    dry_run: bool = False


class SceneMigrationApplyRequest(BaseModel):
    """P2.2: kopia zapasowa manifestów przed migracją jest domyślna i wyłączalna świadomie."""

    backup: bool = True
    decisions: dict[str, str] = Field(default_factory=dict)


class SceneAssetSelectionRequest(BaseModel):
    asset_ids: list[str] = Field(min_length=1)
    rgb_bands: list[int] | None = None


class ScenePrepareRequest(BaseModel):
    rgb_bands: list[int] | None = None
    method: str = "weighted_brovey"
