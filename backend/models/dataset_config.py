from typing import Literal

from pydantic import BaseModel, Field


SplitMode = Literal[
    "random_tile",
    "scene_split",
    "image_block_split",
    "spatial_block_split",
    "class_balanced_spatial",
]


# Który zestaw kafli trafia do datasetu (drabina inkluzywności):
#  - reviewed_sampled: pozytywy + PRÓBKA sprawdzonych pustych wg negative_ratio (domyślne),
#  - all_reviewed:     pozytywy + WSZYSTKIE sprawdzone puste (bez wykluczonych, bez niesprawdzonych),
#  - all:              WSZYSTKIE kafle (z niesprawdzonymi i wykluczonymi) — pełne pokrycie/predykcja.
TileSelection = Literal["reviewed_sampled", "all_reviewed", "all"]


class ClassMerge(BaseModel):
    """Nieniszczące scalenie klas przy budowie datasetu.

    Adnotacje klas z ``members`` są przy generowaniu remapowane na ``into`` (klasa
    wiodąca zachowuje id i nazwę). Źródłowe adnotacje pozostają nietknięte — scalenie
    dotyczy tylko wygenerowanej wersji datasetu, więc łatwo je cofnąć nową wersją.
    """

    into: int  # id klasy wiodącej (zachowanej)
    members: list[int] = Field(default_factory=list)  # id klas wcielanych w `into`


class DatasetConfig(BaseModel):
    train_ratio: float = 0.7
    val_ratio: float = 0.2
    test_ratio: float = 0.1
    min_box_fraction: float = 0.3
    negative_ratio: float = 0.1  # fraction of empty tiles to include (tile_selection="reviewed_sampled")
    # Zakres kafli w datasecie. negative_ratio działa TYLKO dla "reviewed_sampled".
    tile_selection: TileSelection = "reviewed_sampled"
    split_mode: SplitMode = "random_tile"
    split_seed: int = 42
    block_size_tiles: int = 5
    # Szerokosc pasa ochronnego (w szerokosciach kafla) miedzy splitami dla trybow
    # przestrzennych. Kafle treningowe blizej niz to od kafla val/test sa odrzucane,
    # co ogranicza przeciek graniczny (blockCV buffered). 0 = wylaczone (domyslnie).
    spatial_buffer_tiles: int = 0
    preprocessing_profile_id: str | None = None
    # Opt-in: resample every tile to a common ground sample distance (metres) at
    # generation, so a multi-GSD dataset trains at a consistent object scale. None =
    # native pixel tiling (default). Tiles are re-derived fresh from source
    # annotations; per-tile review flags do not apply in this mode.
    target_gsd_m: float | None = Field(default=None, gt=0, le=1000)
    scene_ids: list[str] = Field(default_factory=list)
    class_ids: list[int] = Field(default_factory=list)
    annotator_emails: list[str] = Field(default_factory=list)
    annotation_sources: list[str] = Field(default_factory=list)
    # Scene metadata range filters. Scenes whose value falls outside a set range are
    # excluded from the build; None on a bound leaves that side open. A scene that is
    # missing the metadata is excluded whenever the matching filter is active
    # (unverifiable == out) — incidence is SAR-only, so it drops EO scenes by design.
    gsd_min_m: float | None = Field(default=None, gt=0)
    gsd_max_m: float | None = Field(default=None, gt=0)
    incidence_min_deg: float | None = Field(default=None, ge=0, le=90)
    incidence_max_deg: float | None = Field(default=None, ge=0, le=90)
    acquired_after: str | None = None  # ISO date/datetime, inclusive lower bound
    acquired_before: str | None = None  # ISO date/datetime, inclusive upper bound
    # Nieniszczące scalenia klas (member -> into) stosowane przy generowaniu wersji.
    class_merges: list[ClassMerge] = Field(default_factory=list)
    # P1.5: zgoda na zbudowanie datasetu ze scen o RÓŻNEJ semantyce wartości pikseli
    # (np. nieskalibrowana amplituda ICEYE razem ze skalibrowanym sigma0 Capelli).
    # Domyślnie zabronione: bramka wymaga, żeby takie zmieszanie nie mogło zajść NIEJAWNIE.
    # Ustawienie na True jest decyzją użytkownika i zostaje zapisane w manifeście runu.
    allow_mixed_radiometry: bool = False


class TileSummary(BaseModel):
    total_all: int = 0
    active: int = 0
    reviewed: int = 0
    reviewed_empty: int = 0
    unreviewed: int = 0
    used: int = 0
    positive: int = 0
    negative: int = 0
    excluded: int = 0
    omitted: int = 0


class ClassDatasetStats(BaseModel):
    class_id: int
    name: str
    source_annotations: int = 0
    dataset_annotations: int = 0
    train: int = 0
    val: int = 0
    test: int = 0
    used: bool = False
    source_only: bool = False
    # Faza 1 raportu niezbalansowania:
    tiles: int = 0                 # liczba KAFLI zawierających klasę (nie adnotacji)
    scenes: int = 0                # liczba UNIKALNYCH scen zawierających klasę (dataset-side)
    share_pct: float = 0.0         # dataset_annotations / total_annotations * 100
    rel_to_largest: float = 0.0    # dataset_annotations / max(dataset_annotations)  (0..1)
    train_pct: float = 0.0         # rozkład tej klasy po splitach: train/(train+val+test)*100
    val_pct: float = 0.0
    test_pct: float = 0.0
    missing_in_splits: list[str] = Field(default_factory=list)  # NIEpuste sploty z 0 tej klasy


class SplitDatasetStats(BaseModel):
    split: str
    images: int = 0
    positive_tiles: int = 0
    negative_tiles: int = 0
    annotations: int = 0
    per_class: dict[str, int] = Field(default_factory=dict)


class SceneDatasetStats(BaseModel):
    scene_id: str
    filename: str
    status: str = ""
    tiles: int = 0
    reviewed_tiles: int = 0
    used_tiles: int = 0
    excluded_tiles: int = 0
    annotations: int = 0
    classes: list[str] = Field(default_factory=list)
    without_annotations: bool = False
    without_dataset_classes: bool = False


class GeometryHistogram(BaseModel):
    bins: list[float] = Field(default_factory=list)   # krawędzie binów (len == counts+1)
    counts: list[int] = Field(default_factory=list)


class GeometryClassStats(BaseModel):
    class_id: int
    name: str
    count: int = 0
    # AABB (z tile_annotations, per-kafel; w px kafla i frakcja pola kafla)
    w_px_median: float = 0.0
    h_px_median: float = 0.0
    area_px_median: float = 0.0
    area_px_p90: float = 0.0
    area_frac_median: float = 0.0
    aspect_median: float = 0.0
    size_small: int = 0
    size_medium: int = 0
    size_large: int = 0
    # metry (None, gdy brak GSD dla scen tej klasy)
    w_m_median: float | None = None
    h_m_median: float | None = None
    area_m2_median: float | None = None
    # OBB (z adnotacji źródłowych rotated_bbox; kąt/boki niezmiennicze przy kaflowaniu)
    obb_count: int = 0
    angle_median: float | None = None
    short_side_median: float | None = None
    long_side_median: float | None = None
    near_square_count: int = 0


class GeometryStats(BaseModel):
    mode: str = "bbox"                     # bbox | rotated_bbox
    tile_size: int = 0
    size_thresholds_px: dict[str, float] = Field(default_factory=dict)  # {"small_max","medium_max"} w px²
    gsd_coverage_frac: float = 0.0         # udział adnotacji z policzonymi metrami
    total_annotations: int = 0
    per_class: list[GeometryClassStats] = Field(default_factory=list)
    area_px_hist: GeometryHistogram = Field(default_factory=GeometryHistogram)
    aspect_hist: GeometryHistogram = Field(default_factory=GeometryHistogram)
    angle_hist: GeometryHistogram | None = None   # OBB (stopnie 0..180)


class MetaDist(BaseModel):
    """Rozkład kategoryczny (np. sensor/modality/sezon) z rozbiciem per split."""
    overall: dict[str, int] = Field(default_factory=dict)
    train: dict[str, int] = Field(default_factory=dict)
    val: dict[str, int] = Field(default_factory=dict)
    test: dict[str, int] = Field(default_factory=dict)


class SplitHistogram(BaseModel):
    bins: list[float] = Field(default_factory=list)   # wspólne krawędzie (len == counts+1)
    counts: list[int] = Field(default_factory=list)   # ogółem
    train: list[int] = Field(default_factory=list)
    val: list[int] = Field(default_factory=list)
    test: list[int] = Field(default_factory=list)


class AcquisitionStats(BaseModel):
    weighting: str = "by_tile"          # rozkład ważony liczbą kafli
    total_tiles: int = 0
    gsd_hist: SplitHistogram | None = None
    incidence_hist: SplitHistogram | None = None      # SAR kąt padania
    sensor: MetaDist = Field(default_factory=MetaDist)
    modality: MetaDist = Field(default_factory=MetaDist)
    season: MetaDist = Field(default_factory=MetaDist)
    missing: dict[str, int] = Field(default_factory=dict)  # #kafli bez {gsd, sensor, date}


class CoOccurrence(BaseModel):
    classes: list[str] = Field(default_factory=list)   # kolejność indeksów (Top-K wg #kafli)
    counts: list[list[int]] = Field(default_factory=list)  # symetryczna; diag[i] = #kafli z klasą i
    tiles_total: int = 0               # #kafli z ≥1 klasą (mianownik do lift/Jaccard na FE)
    truncated: bool = False            # True, gdy ograniczono do Top-K klas


class DatasetStats(BaseModel):
    run_id: str | None = None
    run_dir: str = ""
    storage_mode: str = "copy"
    is_latest: bool = True
    preprocessing_profile_id: str | None = None
    preprocessing_profile_hash: str | None = None
    total_tiles: int = 0
    positive_tiles: int = 0
    negative_tiles: int = 0
    total_annotations: int = 0
    largest_class_count: int = 0   # max(dataset_annotations) — mianownik dla rel_to_largest
    per_class: dict[str, int] = Field(default_factory=dict)
    per_split: dict[str, dict] = Field(default_factory=dict)
    dataset_dir: str = ""
    generated_at: str = ""
    split_mode: SplitMode = "random_tile"
    split_seed: int = 42
    tile_summary: TileSummary = Field(default_factory=TileSummary)
    class_stats: list[ClassDatasetStats] = Field(default_factory=list)
    split_stats: list[SplitDatasetStats] = Field(default_factory=list)
    scene_stats: list[SceneDatasetStats] = Field(default_factory=list)
    unused_classes: list[str] = Field(default_factory=list)
    source_only_classes: list[str] = Field(default_factory=list)
    scenes_without_annotations: list[str] = Field(default_factory=list)
    scenes_without_dataset_classes: list[str] = Field(default_factory=list)
    geometry_stats: GeometryStats | None = None
    co_occurrence: CoOccurrence | None = None
    acquisition_stats: AcquisitionStats | None = None
    validation_report: dict = Field(default_factory=dict)
