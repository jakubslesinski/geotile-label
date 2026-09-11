"""Build train/val/test dataset from tiled annotations — multi-scene."""

import io
import os
import random
import shutil
import math
import time
from collections import defaultdict
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Generator, Iterator
from statistics import median

from models.dataset_config import (
    AcquisitionStats,
    ClassDatasetStats,
    CoOccurrence,
    DatasetConfig,
    DatasetStats,
    MetaDist,
    SplitHistogram,
    GeometryClassStats,
    GeometryHistogram,
    GeometryStats,
    SceneDatasetStats,
    SplitDatasetStats,
    TileSummary,
)
from models.tiling_config import TileInfo
from models.preprocessing import PreprocessingProfile
from services.preprocessing_profiles import (
    apply_preprocessing_profile,
    open_scene_source,
    read_tile_from_source,
)
from services.attribute_engine.engine import pixel_to_native, transform_points

from PIL import Image

SPLITS = ("train", "val", "test")


class DatasetBuildCancelled(RuntimeError):
    """Cooperative cancellation observed at a bounded dataset batch boundary."""


def build_dataset(
    tiles: list[TileInfo],
    tile_annotations: dict[str, list[list[float]]],
    tile_source_map: dict[str, str],
    dataset_dir: Path,
    config: DatasetConfig,
    class_names: dict[int, str],
    scene_context: dict[str, dict] | None = None,
    source_annotations_by_scene: dict[str, list[dict]] | None = None,
    scene_source_map: dict[str, str] | None = None,
    preprocessing_profile: dict | None = None,
    tile_size: int = 640,
    tile_annotation_links: list[dict] | None = None,
    scene_manifests: dict[str, dict] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    pipeline_metrics: dict[str, Any] | None = None,
) -> Generator[dict, None, DatasetStats]:
    """
    Split tiles into train/val/test and copy files.
    tile_source_map: {prefixed_filename: source_directory_path}
    Yields progress dicts, returns DatasetStats.
    """
    rng = random.Random(config.split_seed)
    scene_source_map = scene_source_map or {}

    if dataset_dir.exists():
        shutil.rmtree(dataset_dir)
    dataset_dir.mkdir(parents=True, exist_ok=True)

    active_tiles = [tile for tile in tiles if not _is_excluded_tile(tile)]
    positive_tiles = [t for t in active_tiles if tile_annotations.get(t.filename)]
    confirmed_negative = [
        t
        for t in active_tiles
        if _is_reviewed_tile(t) and not tile_annotations.get(t.filename)
    ]

    tile_selection = getattr(config, "tile_selection", "reviewed_sampled")
    if tile_selection == "all":
        # Pełne pokrycie: WSZYSTKIE kafle, łącznie z niesprawdzonymi i wykluczonymi.
        # Do eksportu/predykcji — puste kafle wchodzą jako tło (pusty plik etykiet).
        selected_tiles = list(tiles)
    elif tile_selection == "all_reviewed":
        # Wszystkie świadomie obejrzane kafle bez limitu: pozytywy + wszystkie sprawdzone
        # puste. Wykluczone i niesprawdzone pomijane.
        selected_tiles = positive_tiles + confirmed_negative
    else:  # "reviewed_sampled" (domyślne): pozytywy + próbka sprawdzonych pustych wg udziału
        n_neg = int(len(positive_tiles) * config.negative_ratio)
        sampled_negative = _sample_tiles(confirmed_negative, n_neg, rng)
        selected_tiles = positive_tiles + sampled_negative
    splits = _assign_splits(
        selected_tiles,
        tile_annotations,
        config,
        rng,
        tile_annotation_links=tile_annotation_links or [],
        scene_manifests=scene_manifests or {},
        tile_size=tile_size,
    )
    # Pas ochronny miedzy splitami przestrzennymi (blockCV buffered) — bez efektu,
    # gdy spatial_buffer_tiles=0 albo tryb nie jest przestrzenny.
    selected_tiles, splits = _apply_spatial_buffer(
        selected_tiles, splits, config, scene_manifests or {}, tile_size
    )

    for split in SPLITS:
        (dataset_dir / split / "images").mkdir(parents=True, exist_ok=True)
        (dataset_dir / split / "labels").mkdir(parents=True, exist_ok=True)

    total = len(selected_tiles)
    stats_state = _empty_stats_state(class_names)

    profile_model = None
    if preprocessing_profile:
        profile_model = (
            preprocessing_profile
            if isinstance(preprocessing_profile, PreprocessingProfile)
            else PreprocessingProfile(**preprocessing_profile)
        )

    # Grupuj kafle po scenie, żeby dekodować/otwierać każde źródło RAZ na scenę (a nie raz
    # na kafel). Kolejność wewnątrz sceny deterministyczna (y0, x0). Przypisanie splitu i
    # akumulacja statystyk są niezależne od kolejności, więc regrupowanie jest bezpieczne.
    tiles_by_scene: dict[str, list[TileInfo]] = defaultdict(list)
    for tile in selected_tiles:
        tiles_by_scene[_scene_id_from_tile(tile)].append(tile)

    write_workers = _dataset_write_workers()
    memory_budget_bytes = _dataset_memory_budget_bytes()
    estimated_tile_bytes = max(1, tile_size * tile_size * _DATASET_ESTIMATED_BYTES_PER_PIXEL)
    # Dwie bufory są żywe jednocześnie: workerzy kończą partię N, gdy główny wątek
    # czyta N+1. Nie pozwalamy, żeby ich konserwatywny koszt przekroczył budżet.
    batch_size = max(
        1,
        min(write_workers, memory_budget_bytes // max(1, 2 * estimated_tile_bytes)),
    )
    timings = {
        "read_seconds": 0.0,
        "preprocess_seconds": 0.0,
        "encode_seconds": 0.0,
        "write_seconds": 0.0,
        "label_seconds": 0.0,
    }
    wall_started = time.perf_counter()
    idx = 0
    pool = ThreadPoolExecutor(max_workers=write_workers, thread_name_prefix="dataset-write")
    try:
        for scene_id in sorted(tiles_by_scene):
            _raise_if_cancelled(should_cancel)
            scene_tiles = sorted(tiles_by_scene[scene_id], key=lambda t: (t.y0, t.x0))
            scene_source = scene_source_map.get(scene_id)
            reader = None
            if scene_source and profile_model is not None:
                reader = open_scene_source(scene_source, profile_model)

            pending: list[tuple[TileInfo, str, Future[dict[str, float]]]] | None = None
            try:
                for batch in _chunk(scene_tiles, batch_size):
                    _raise_if_cancelled(should_cancel)
                    current_payloads: list[
                        tuple[TileInfo, str, tuple[Any, Any, Path] | tuple[Path, Path]]
                    ] = []

                    # Reader GDAL pozostaje wyłącznie w tym wątku. W tym czasie pula
                    # preprocessuje/koduje/zapisuje poprzednią partię (double buffering).
                    if reader is not None:
                        read_started = time.perf_counter()
                        for tile in batch:
                            split = splits.get(tile.filename, "train")
                            raw, valid_mask = read_tile_from_source(
                                reader,
                                tile.x0,
                                tile.y0,
                                tile_size,
                                window_px=tile.window_px,
                            )
                            current_payloads.append(
                                (
                                    tile,
                                    split,
                                    (
                                        raw,
                                        valid_mask,
                                        dataset_dir / split / "images" / tile.filename,
                                    ),
                                )
                            )
                        timings["read_seconds"] += time.perf_counter() - read_started
                    else:
                        for tile in batch:
                            split = splits.get(tile.filename, "train")
                            src = _resolve_source_tile_path(tile, tile_source_map)
                            if not src.exists():
                                raise FileNotFoundError(
                                    f"Cannot materialize tile {tile.filename}: source scene and legacy tile are unavailable"
                                )
                            current_payloads.append(
                                (
                                    tile,
                                    split,
                                    (src, dataset_dir / split / "images" / tile.filename),
                                )
                            )

                    completed = _collect_image_batch(pending, timings) if pending else []
                    _raise_if_cancelled(should_cancel)

                    # Uruchom N+1 przed deterministycznym commitem N. Dzięki temu pula pracuje
                    # również podczas zapisu etykiet i obsługi zdarzeń postępu.
                    pending = []
                    for tile, split, payload in current_payloads:
                        if reader is not None:
                            future = pool.submit(_write_tile_image, payload, profile_model)
                        else:
                            future = pool.submit(_copy_tile_image, payload)
                        pending.append((tile, split, future))

                    for tile, split in completed:
                        label_started = time.perf_counter()
                        anns = tile_annotations.get(tile.filename, [])
                        _update_stats_state(stats_state, tile, split, anns, class_names)
                        _write_tile_label(dataset_dir, split, tile, anns)
                        timings["label_seconds"] += time.perf_counter() - label_started
                        idx += 1
                        yield {"done": idx, "total": total, "split": split}

                completed = _collect_image_batch(pending, timings) if pending else []
                _raise_if_cancelled(should_cancel)
                for tile, split in completed:
                    label_started = time.perf_counter()
                    anns = tile_annotations.get(tile.filename, [])
                    _update_stats_state(stats_state, tile, split, anns, class_names)
                    _write_tile_label(dataset_dir, split, tile, anns)
                    timings["label_seconds"] += time.perf_counter() - label_started
                    idx += 1
                    yield {"done": idx, "total": total, "split": split}
            finally:
                if reader is not None:
                    reader.close()
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
        if pipeline_metrics is not None:
            pipeline_metrics.clear()
            pipeline_metrics.update(
                {
                    "schema_version": 1,
                    "tiles": idx,
                    "scenes": len(tiles_by_scene),
                    "worker_count": write_workers,
                    "executor_count": 1,
                    "batch_size": batch_size,
                    "max_buffered_tiles": batch_size * 2,
                    "memory_budget_bytes": memory_budget_bytes,
                    "estimated_bytes_per_tile": estimated_tile_bytes,
                    "estimated_peak_buffer_bytes": batch_size * 2 * estimated_tile_bytes,
                    "wall_seconds": round(time.perf_counter() - wall_started, 6),
                    **{key: round(value, 6) for key, value in timings.items()},
                }
            )

    return compute_dataset_statistics(
        tiles=tiles,
        selected_tiles=selected_tiles,
        tile_annotations=tile_annotations,
        splits=splits,
        dataset_dir=dataset_dir,
        config=config,
        class_names=class_names,
        stats_state=stats_state,
        scene_context=scene_context or {},
        source_annotations_by_scene=source_annotations_by_scene or {},
        tile_size=tile_size,
    )


def compute_dataset_statistics(
    tiles: list[TileInfo],
    selected_tiles: list[TileInfo],
    tile_annotations: dict[str, list[list[float]]],
    splits: dict[str, str],
    dataset_dir: Path,
    config: DatasetConfig,
    class_names: dict[int, str],
    stats_state: dict | None = None,
    scene_context: dict[str, dict] | None = None,
    source_annotations_by_scene: dict[str, list[dict]] | None = None,
    tile_size: int = 640,
) -> DatasetStats:
    scene_context = scene_context or {}
    source_annotations_by_scene = source_annotations_by_scene or {}
    stats_state = stats_state or _build_stats_state_from_dataset(
        selected_tiles, tile_annotations, splits, class_names
    )

    selected_names = {t.filename for t in selected_tiles}
    excluded_count = sum(1 for t in tiles if _is_excluded_tile(t))
    active_count = len(tiles) - excluded_count
    reviewed_count = sum(1 for t in tiles if not _is_excluded_tile(t) and _is_reviewed_tile(t))
    reviewed_empty_count = sum(
        1
        for t in tiles
        if not _is_excluded_tile(t) and _is_reviewed_tile(t) and not tile_annotations.get(t.filename)
    )
    unreviewed_count = max(0, active_count - reviewed_count)
    positive_count = sum(1 for t in selected_tiles if tile_annotations.get(t.filename))
    negative_count = len(selected_tiles) - positive_count
    omitted_count = max(0, active_count - len(selected_tiles))

    source_counts, source_scene_counts, source_scene_classes = _source_annotation_stats(
        source_annotations_by_scene, class_names
    )

    class_stats = _build_class_stats(class_names, source_counts, stats_state)
    split_stats = _build_split_stats(stats_state)
    scene_stats = _build_scene_stats(
        tiles,
        selected_names,
        tile_annotations,
        scene_context,
        source_scene_counts,
        source_scene_classes,
        class_names,
    )

    unused_classes = [
        stat.name
        for stat in class_stats
        if stat.class_id in class_names and stat.dataset_annotations == 0
    ]
    source_only_classes = [stat.name for stat in class_stats if stat.source_only]
    scenes_without_annotations = [
        stat.filename for stat in scene_stats if stat.without_annotations
    ]
    scenes_without_dataset_classes = [
        stat.filename for stat in scene_stats if stat.without_dataset_classes
    ]

    gsd_by_scene = {
        sid: (ctx or {}).get("gsd_m") for sid, ctx in scene_context.items()
    }
    geometry_stats = _build_geometry_stats(
        selected_tiles, tile_annotations, class_names, tile_size,
        gsd_by_scene, source_annotations_by_scene,
    )
    co_occurrence = _build_co_occurrence(stats_state)
    acquisition_stats = _build_acquisition_stats(selected_tiles, splits, scene_context)

    per_split = {
        stat.split: {
            "images": stat.images,
            "annotations": stat.annotations,
            "positive_tiles": stat.positive_tiles,
            "negative_tiles": stat.negative_tiles,
            "per_class": stat.per_class,
        }
        for stat in split_stats
    }

    return DatasetStats(
        total_tiles=len(selected_tiles),
        positive_tiles=positive_count,
        negative_tiles=negative_count,
        total_annotations=sum(stats_state["dataset_counts"].values()),
        largest_class_count=max((int(v) for v in stats_state["dataset_counts"].values()), default=0),
        per_class=dict(stats_state["dataset_counts_by_name"]),
        per_split=per_split,
        dataset_dir=str(dataset_dir),
        generated_at=datetime.now(timezone.utc).isoformat(),
        split_mode=config.split_mode,
        split_seed=config.split_seed,
        tile_summary=TileSummary(
            total_all=len(tiles),
            active=active_count,
            reviewed=reviewed_count,
            reviewed_empty=reviewed_empty_count,
            unreviewed=unreviewed_count,
            used=len(selected_tiles),
            positive=positive_count,
            negative=negative_count,
            excluded=excluded_count,
            omitted=omitted_count,
        ),
        class_stats=class_stats,
        split_stats=split_stats,
        scene_stats=scene_stats,
        unused_classes=unused_classes,
        source_only_classes=source_only_classes,
        scenes_without_annotations=scenes_without_annotations,
        scenes_without_dataset_classes=scenes_without_dataset_classes,
        geometry_stats=geometry_stats,
        co_occurrence=co_occurrence,
        acquisition_stats=acquisition_stats,
    )


def _assign_splits(
    tiles: list[TileInfo],
    tile_annotations: dict[str, list[list[float]]],
    config: DatasetConfig,
    rng: random.Random,
    tile_annotation_links: list[dict] | None = None,
    scene_manifests: dict[str, dict] | None = None,
    tile_size: int = 640,
) -> dict[str, str]:
    if not tiles:
        return {}

    if config.split_mode == "scene_split":
        groups = _group_by_scene(tiles)
        groups = _merge_groups_by_source_annotations(groups, tile_annotation_links or [])
        return _assign_grouped_splits(groups, config, rng)
    if config.split_mode == "image_block_split":
        groups = _group_by_image_block(tiles, config)
        groups = _merge_groups_by_source_annotations(groups, tile_annotation_links or [])
        return _assign_grouped_splits(groups, config, rng)
    if config.split_mode == "spatial_block_split":
        groups = _group_by_geospatial_block(
            tiles, config, scene_manifests or {}, tile_size
        )
        groups = _merge_groups_by_source_annotations(groups, tile_annotation_links or [])
        return _assign_grouped_splits(groups, config, rng)
    if config.split_mode == "class_balanced_spatial":
        groups = _group_by_geospatial_block(
            tiles, config, scene_manifests or {}, tile_size
        )
        groups = _merge_groups_by_source_annotations(groups, tile_annotation_links or [])
        return _assign_class_balanced_splits(
            groups, tile_annotations, config
        )
    return _assign_random_tile_splits(tiles, config, rng)


def _assign_random_tile_splits(
    tiles: list[TileInfo],
    config: DatasetConfig,
    rng: random.Random,
) -> dict[str, str]:
    ordered = list(tiles)
    rng.shuffle(ordered)
    n_train, n_val = _split_cutoffs(len(ordered), config)

    result = {}
    for idx, tile in enumerate(ordered):
        if idx < n_train:
            split = "train"
        elif idx < n_train + n_val:
            split = "val"
        else:
            split = "test"
        result[tile.filename] = split
    return result


def _assign_grouped_splits(
    groups: list[list[TileInfo]],
    config: DatasetConfig,
    rng: random.Random,
) -> dict[str, str]:
    ordered = list(groups)
    rng.shuffle(ordered)
    total = sum(len(group) for group in ordered)
    if total == 0:
        return {}

    n_train, n_val = _split_cutoffs(total, config)
    targets = {
        "train": n_train,
        "val": n_val,
        "test": max(0, total - n_train - n_val),
    }

    # Source-annotation merging can fuse many spatial blocks into one dominant
    # group. A sequential train -> val -> test fill would dump that group into
    # whichever split it landed in and leave too little for the rest (empty
    # test). Instead we place the largest groups first, each into the split that
    # is proportionally least full, so every split keeps its share regardless of
    # how uneven the group sizes are.
    order = sorted(ordered, key=len, reverse=True)
    wanted = [split for split in SPLITS if targets[split] > 0] or ["train"]
    counts = {"train": 0, "val": 0, "test": 0}
    result: dict[str, str] = {}

    for group in order:
        split = min(
            wanted,
            key=lambda s: (counts[s] / targets[s], -targets[s]),
        )
        for tile in group:
            result[tile.filename] = split
        counts[split] += len(group)

    _ensure_nonempty_test(result, counts, targets, order)
    return result


def _ensure_nonempty_test(
    result: dict[str, str],
    counts: dict[str, int],
    targets: dict[str, int],
    groups: list[list[TileInfo]],
) -> None:
    """Guarantee a non-empty test split when one was requested.

    With very few groups (e.g. a single dominant merged component) proportional
    filling can still leave test empty. Move the smallest whole group out of the
    most-populated donor split so a final test evaluation stays possible, but
    never empty the donor itself.
    """
    if targets.get("test", 0) <= 0 or counts.get("test", 0) > 0:
        return
    donor = max(("train", "val"), key=lambda s: counts.get(s, 0))
    donor_groups = [g for g in groups if g and result.get(g[0].filename) == donor]
    if len(donor_groups) < 2:
        return
    victim = min(donor_groups, key=len)
    for tile in victim:
        result[tile.filename] = "test"
    counts[donor] -= len(victim)
    counts["test"] = counts.get("test", 0) + len(victim)


def _assign_class_balanced_splits(
    groups: list[list[TileInfo]],
    tile_annotations: dict[str, list[list[float]]],
    config: DatasetConfig,
) -> dict[str, str]:
    total = sum(len(group) for group in groups)
    if total == 0:
        return {}

    targets = {
        "train": max(1, int(total * config.train_ratio)),
        "val": max(0, int(total * config.val_ratio)),
        "test": max(0, total - int(total * config.train_ratio) - int(total * config.val_ratio)),
    }
    group_payloads = []
    for group in groups:
        class_counts: dict[int, int] = defaultdict(int)
        annotation_count = 0
        for tile in group:
            for ann in tile_annotations.get(tile.filename, []):
                cls_id = int(ann[0])
                class_counts[cls_id] += 1
                annotation_count += 1
        group_payloads.append((group, dict(class_counts), annotation_count))

    group_payloads.sort(
        key=lambda item: (
            -item[2],
            -len(item[0]),
            _scene_id_from_tile(item[0][0]) if item[0] else "",
            item[0][0].row if item[0] else 0,
            item[0][0].col if item[0] else 0,
        )
    )

    counts = {"train": 0, "val": 0, "test": 0}
    class_counts_by_split: dict[str, dict[int, int]] = {
        split: defaultdict(int) for split in SPLITS
    }
    result = {}

    for group, group_classes, _annotation_count in group_payloads:
        best_split = min(
            SPLITS,
            key=lambda split: _class_balance_score(
                split, len(group), group_classes, counts, class_counts_by_split, targets
            ),
        )
        for tile in group:
            result[tile.filename] = best_split
        counts[best_split] += len(group)
        for cls_id, count in group_classes.items():
            class_counts_by_split[best_split][cls_id] += count

    return result


def _class_balance_score(
    split: str,
    group_size: int,
    group_classes: dict[int, int],
    counts: dict[str, int],
    class_counts_by_split: dict[str, dict[int, int]],
    targets: dict[str, int],
) -> tuple[float, float, int]:
    target = max(1, targets.get(split, 0))
    overflow = max(0, counts[split] + group_size - target)
    size_pressure = (counts[split] + group_size) / target
    class_pressure = sum(
        class_counts_by_split[split].get(cls_id, 0) + count
        for cls_id, count in group_classes.items()
    )
    return overflow, size_pressure, class_pressure


def _group_by_scene(tiles: list[TileInfo]) -> list[list[TileInfo]]:
    groups: dict[str, list[TileInfo]] = defaultdict(list)
    for tile in tiles:
        groups[_scene_id_from_tile(tile)].append(tile)
    return list(groups.values())


def _group_by_image_block(
    tiles: list[TileInfo],
    config: DatasetConfig,
) -> list[list[TileInfo]]:
    block_size = max(1, config.block_size_tiles)
    groups: dict[tuple[str, int, int], list[TileInfo]] = defaultdict(list)
    for tile in tiles:
        key = (
            _scene_id_from_tile(tile),
            max(0, int(tile.col) - 1) // block_size,
            max(0, int(tile.row) - 1) // block_size,
        )
        groups[key].append(tile)
    return list(groups.values())


def _group_by_geospatial_block(
    tiles: list[TileInfo],
    config: DatasetConfig,
    scene_manifests: dict[str, dict],
    tile_size: int,
) -> list[list[TileInfo]]:
    positions: dict[str, tuple[float, float]] = {}
    spans: list[float] = []
    for tile in tiles:
        scene_id = _scene_id_from_tile(tile)
        position, span = _tile_web_mercator_position(
            tile, scene_manifests.get(scene_id, {}), tile_size
        )
        if position is not None:
            positions[tile.filename] = position
        if span is not None and span > 0:
            spans.append(span)

    if not positions or not spans:
        return _group_by_image_block(tiles, config)

    block_span_m = max(1.0, float(median(spans)) * max(1, config.block_size_tiles))
    groups: dict[tuple, list[TileInfo]] = defaultdict(list)
    for tile in tiles:
        position = positions.get(tile.filename)
        if position is None:
            key = (
                "image-fallback",
                _scene_id_from_tile(tile),
                max(0, int(tile.col) - 1) // max(1, config.block_size_tiles),
                max(0, int(tile.row) - 1) // max(1, config.block_size_tiles),
            )
        else:
            key = (
                "web-mercator",
                math.floor(position[0] / block_span_m),
                math.floor(position[1] / block_span_m),
            )
        groups[key].append(tile)
    return list(groups.values())


def _tile_web_mercator_position(
    tile: TileInfo,
    manifest: dict,
    tile_size: int,
) -> tuple[tuple[float, float] | None, float | None]:
    geospatial = manifest.get("geospatial") or {}
    transform_values = geospatial.get("transform")
    crs = geospatial.get("crs")
    if not geospatial.get("has_geo") or not transform_values or not crs:
        return None, None
    try:
        center_px = [tile.x0 + tile_size / 2.0, tile.y0 + tile_size / 2.0]
        corners_px = [
            [tile.x0, tile.y0],
            [tile.x0 + tile_size, tile.y0],
            [tile.x0, tile.y0 + tile_size],
        ]
        native = [pixel_to_native(point, transform_values) for point in [center_px, *corners_px]]
        mercator = transform_points(native, crs, "EPSG:3857")
        center, origin, right, bottom = mercator
        width = math.hypot(right[0] - origin[0], right[1] - origin[1])
        height = math.hypot(bottom[0] - origin[0], bottom[1] - origin[1])
        return (float(center[0]), float(center[1])), max(width, height)
    except Exception:
        return None, None


_SPATIAL_SPLIT_MODES = {"spatial_block_split", "class_balanced_spatial"}


def _apply_spatial_buffer(
    selected_tiles: list[TileInfo],
    splits: dict[str, str],
    config: DatasetConfig,
    scene_manifests: dict[str, dict],
    tile_size: int,
) -> tuple[list[TileInfo], dict[str, str]]:
    """Odrzuc kafle treningowe w pasie ochronnym wokol kafli val/test.

    Ogranicza przeciek graniczny w splitach przestrzennych: po podziale sasiadujace
    kafle z train i z val/test moga lezec tuz obok siebie. Usuwamy strone treningowa,
    zachowujac val/test w calosci. Zwraca przefiltrowana liste i slownik splitow.

    Kubelkowanie po siatce o boku bufora daje O(N): kandydatow na sasiada szuka sie
    tylko w komorce kafla i osmiu sasiednich.
    """
    if config.split_mode not in _SPATIAL_SPLIT_MODES or config.spatial_buffer_tiles <= 0:
        return selected_tiles, splits

    positions: dict[str, tuple[float, float]] = {}
    spans: list[float] = []
    for tile in selected_tiles:
        scene_id = _scene_id_from_tile(tile)
        position, span = _tile_web_mercator_position(
            tile, scene_manifests.get(scene_id, {}), tile_size
        )
        if position is not None:
            positions[tile.filename] = position
        if span is not None and span > 0:
            spans.append(span)
    if not positions or not spans:
        return selected_tiles, splits

    # Srodki sasiednich kafli sa oddalone o ~1 rozpietosc, wiec prog liczymy z pol-
    # kaflowa tolerancja: bufor N usuwa kafle do N-tego pierscienia wlacznie (odleglosc
    # srodkow do (N+0.5) rozpietosci), a nie pomija stykajacych sie przez rownosc.
    buffer_m = float(median(spans)) * (config.spatial_buffer_tiles + 0.5)
    cell = max(buffer_m, 1.0)

    # Kubelki tylko z kaflami val/test — to od nich mierzymy odleglosc.
    holdout_buckets: dict[tuple[int, int], list[tuple[float, float]]] = defaultdict(list)
    for tile in selected_tiles:
        if splits.get(tile.filename) in ("val", "test"):
            pos = positions.get(tile.filename)
            if pos is not None:
                holdout_buckets[(int(pos[0] // cell), int(pos[1] // cell))].append(pos)

    if not holdout_buckets:
        return selected_tiles, splits

    buffer_sq = buffer_m * buffer_m
    dropped: set[str] = set()
    for tile in selected_tiles:
        if splits.get(tile.filename) != "train":
            continue
        pos = positions.get(tile.filename)
        if pos is None:
            continue
        cx, cy = int(pos[0] // cell), int(pos[1] // cell)
        too_close = False
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for hx, hy in holdout_buckets.get((cx + dx, cy + dy), ()):  # noqa: E741
                    if (pos[0] - hx) ** 2 + (pos[1] - hy) ** 2 < buffer_sq:
                        too_close = True
                        break
                if too_close:
                    break
            if too_close:
                break
        if too_close:
            dropped.add(tile.filename)

    if not dropped:
        return selected_tiles, splits

    filtered = [tile for tile in selected_tiles if tile.filename not in dropped]
    remaining_splits = {name: split for name, split in splits.items() if name not in dropped}
    return filtered, remaining_splits


def _merge_groups_by_source_annotations(
    groups: list[list[TileInfo]],
    tile_annotation_links: list[dict],
) -> list[list[TileInfo]]:
    if len(groups) < 2 or not tile_annotation_links:
        return groups

    tile_group: dict[str, int] = {}
    for index, group in enumerate(groups):
        for tile in group:
            tile_group[tile.filename] = index

    parents = list(range(len(groups)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parents[right_root] = left_root

    source_groups: dict[str, set[int]] = defaultdict(set)
    for link in tile_annotation_links:
        source_id = link.get("source_annotation_id")
        filename = link.get("dataset_tile_filename")
        group_index = tile_group.get(str(filename)) if filename else None
        if source_id and group_index is not None:
            source_groups[str(source_id)].add(group_index)

    for group_indexes in source_groups.values():
        ordered = sorted(group_indexes)
        for group_index in ordered[1:]:
            union(ordered[0], group_index)

    merged: dict[int, list[TileInfo]] = defaultdict(list)
    for index, group in enumerate(groups):
        merged[find(index)].extend(group)
    return list(merged.values())


def _split_cutoffs(n: int, config: DatasetConfig) -> tuple[int, int]:
    n_train = int(n * config.train_ratio)
    n_val = int(n * config.val_ratio)
    return n_train, n_val


def _sample_tiles(
    tiles: list[TileInfo],
    count: int,
    rng: random.Random,
) -> list[TileInfo]:
    ordered = list(tiles)
    rng.shuffle(ordered)
    return ordered[: min(max(count, 0), len(ordered))]


def _resolve_source_tile_path(tile: TileInfo, tile_source_map: dict[str, str]) -> Path:
    source_dir = tile_source_map.get(tile.filename)
    if source_dir:
        original_filename = tile.filename.split("__", 1)[1] if "__" in tile.filename else tile.filename
        return Path(source_dir) / original_filename
    return Path(tile.filename)


def _scene_id_from_tile(tile: TileInfo) -> str:
    return tile.filename.split("__", 1)[0] if "__" in tile.filename else ""


# --- Zrównoleglenie zapisu kafli (DESIGN_DECISIONS.md, performance-audit E7) ---
#
# Profil pętli budowy (xView3, tile_size 1024, profil `sar_linear_percentile`, 400 kafli):
# odczyt 15,5% · preprocessing 44,4% · kodowanie PNG 39,1% · etykiety+open 1,0%.
# Czyli 83,5% to praca CPU, a zmierzone przyspieszenie tej części w wątkach: 2,73× (4 wątki),
# 4,10× (8), 4,27× (14) — plateau przy 8, bo `apply_preprocessing_profile` nie zwalnia GIL-a
# w pełni. Dlatego DOMYŚLNIE 8: dalsze wątki nie dają zysku, a każdy podnosi szczytowy RAM.
_DATASET_WRITE_WORKERS_DEFAULT = 8
_DATASET_MEMORY_BUDGET_MIB_DEFAULT = 512
# raw uint16 RGB + valid mask + RGB output + encoded PNG + allocator overhead.
_DATASET_ESTIMATED_BYTES_PER_PIXEL = 16


def _dataset_write_workers() -> int:
    override = os.environ.get("GEOTILE_DATASET_WRITE_WORKERS")
    if override:
        try:
            return max(1, int(override))
        except ValueError:
            pass  # bezsensowna wartość nie może wywalić budowy
    return max(1, min(_DATASET_WRITE_WORKERS_DEFAULT, os.cpu_count() or 1))


def _dataset_memory_budget_bytes() -> int:
    override = os.environ.get("GEOTILE_DATASET_RAM_MIB")
    if override:
        try:
            return max(1, int(float(override) * 1024 * 1024))
        except ValueError:
            pass
    return _DATASET_MEMORY_BUDGET_MIB_DEFAULT * 1024 * 1024


def _chunk(items: list[TileInfo], size: int) -> Iterator[list[TileInfo]]:
    step = max(1, size)
    for start in range(0, len(items), step):
        yield items[start : start + step]


def _write_tile_image(
    item: tuple[Any, Any, Path],
    profile_model: PreprocessingProfile,
) -> dict[str, float]:
    """Przetwórz i zapisz JEDEN kafel. Bez stanu współdzielonego — bezpieczne w puli."""
    raw, valid_mask, dst_img = item
    started = time.perf_counter()
    processed = apply_preprocessing_profile(raw, profile_model, valid_mask)
    preprocess_seconds = time.perf_counter() - started

    started = time.perf_counter()
    encoded = io.BytesIO()
    Image.fromarray(processed, mode="RGB").save(encoded, "PNG")
    encode_seconds = time.perf_counter() - started

    started = time.perf_counter()
    dst_img.write_bytes(encoded.getbuffer())
    write_seconds = time.perf_counter() - started
    return {
        "preprocess_seconds": preprocess_seconds,
        "encode_seconds": encode_seconds,
        "write_seconds": write_seconds,
    }


def _copy_tile_image(item: tuple[Path, Path]) -> dict[str, float]:
    """Skopiuj legacy tile z osobnym pomiarem odczytu i zapisu."""
    src, dst = item
    started = time.perf_counter()
    payload = src.read_bytes()
    read_seconds = time.perf_counter() - started
    started = time.perf_counter()
    dst.write_bytes(payload)
    write_seconds = time.perf_counter() - started
    return {
        "read_seconds": read_seconds,
        "preprocess_seconds": 0.0,
        "encode_seconds": 0.0,
        "write_seconds": write_seconds,
    }


def _collect_image_batch(
    pending: list[tuple[TileInfo, str, Future[dict[str, float]]]],
    timings: dict[str, float],
) -> list[tuple[TileInfo, str]]:
    """Pobierz wyniki w kolejności wejścia i zagreguj czasy pracy workerów."""
    completed: list[tuple[TileInfo, str]] = []
    for tile, split, future in pending:
        result = future.result()
        for key in ("read_seconds", "preprocess_seconds", "encode_seconds", "write_seconds"):
            timings[key] += float(result.get(key, 0.0))
        completed.append((tile, split))
    return completed


def _write_tile_label(
    dataset_dir: Path,
    split: str,
    tile: TileInfo,
    annotations: list[list[float]],
) -> None:
    label_file = dataset_dir / split / "labels" / tile.filename.replace(".png", ".txt")
    with label_file.open("w", encoding="utf-8", newline="\n") as handle:
        for ann in annotations:
            cls_id = int(ann[0])
            handle.write(f"{cls_id} {ann[1]} {ann[2]} {ann[3]} {ann[4]}\n")


def _raise_if_cancelled(should_cancel: Callable[[], bool] | None) -> None:
    if should_cancel is not None and should_cancel():
        raise DatasetBuildCancelled("Dataset build cancelled")


def _empty_stats_state(class_names: dict[int, str]) -> dict:
    return {
        "dataset_counts": defaultdict(int),
        "dataset_counts_by_name": defaultdict(int),
        "split_stats": {
            split: {
                "images": 0,
                "positive_tiles": 0,
                "negative_tiles": 0,
                "annotations": 0,
                "per_class": defaultdict(int),
            }
            for split in SPLITS
        },
        "scene_used_tiles": defaultdict(int),
        "scene_dataset_classes": defaultdict(set),
        "tiles_by_class": defaultdict(int),  # #kafli zawierających klasę (per class_name)
        "co_pairs": defaultdict(int),        # {(a,b) a<b: #kafli z obiema klasami}
        "co_tiles_total": 0,                 # #kafli z ≥1 klasą
    }


def _update_stats_state(
    state: dict,
    tile: TileInfo,
    split: str,
    anns: list[list[float]],
    class_names: dict[int, str],
) -> None:
    split_data = state["split_stats"][split]
    split_data["images"] += 1
    split_data["annotations"] += len(anns)
    if anns:
        split_data["positive_tiles"] += 1
    else:
        split_data["negative_tiles"] += 1

    scene_id = _scene_id_from_tile(tile)
    state["scene_used_tiles"][scene_id] += 1

    tile_classes: set[str] = set()
    for ann in anns:
        cls_id = int(ann[0])
        cls_name = class_names.get(cls_id, str(cls_id))
        state["dataset_counts"][cls_id] += 1
        state["dataset_counts_by_name"][cls_name] += 1
        split_data["per_class"][cls_name] += 1
        state["scene_dataset_classes"][scene_id].add(cls_name)
        tile_classes.add(cls_name)
    # #kafli-per-klasa: liczymy raz na kafel, niezależnie od liczby adnotacji tej klasy.
    for cls_name in tile_classes:
        state["tiles_by_class"][cls_name] += 1
    # Współwystępowanie: pary klas obecnych na tym samym kaflu (nieuporządkowane a<b).
    if tile_classes:
        state["co_tiles_total"] += 1
        ordered = sorted(tile_classes)
        for i in range(len(ordered)):
            for j in range(i + 1, len(ordered)):
                state["co_pairs"][(ordered[i], ordered[j])] += 1


def _build_stats_state_from_dataset(
    selected_tiles: list[TileInfo],
    tile_annotations: dict[str, list[list[float]]],
    splits: dict[str, str],
    class_names: dict[int, str],
) -> dict:
    state = _empty_stats_state(class_names)
    for tile in selected_tiles:
        split = splits.get(tile.filename, "train")
        _update_stats_state(state, tile, split, tile_annotations.get(tile.filename, []), class_names)
    return state


def _source_annotation_stats(
    source_annotations_by_scene: dict[str, list[dict]],
    class_names: dict[int, str],
) -> tuple[dict[int, int], dict[str, int], dict[str, set[str]]]:
    source_counts: dict[int, int] = defaultdict(int)
    scene_counts: dict[str, int] = defaultdict(int)
    scene_classes: dict[str, set[str]] = defaultdict(set)

    for scene_id, annotations in source_annotations_by_scene.items():
        for ann in annotations:
            if ann.get("is_negative", False):
                continue
            cls_id = int(ann.get("class_id", -1))
            if cls_id < 0:
                continue
            cls_name = class_names.get(cls_id, str(cls_id))
            source_counts[cls_id] += 1
            scene_counts[scene_id] += 1
            scene_classes[scene_id].add(cls_name)

    return source_counts, scene_counts, scene_classes


def _build_class_stats(
    class_names: dict[int, str],
    source_counts: dict[int, int],
    stats_state: dict,
) -> list[ClassDatasetStats]:
    dataset_counts = stats_state["dataset_counts"]
    split_class_counts: dict[str, dict[str, int]] = {
        split: stats_state["split_stats"][split]["per_class"]
        for split in SPLITS
    }
    tiles_by_class: dict[str, int] = stats_state.get("tiles_by_class", {})
    # klasa -> #unikalnych scen (odwrócenie scena->{klasy})
    scenes_by_class: dict[str, int] = defaultdict(int)
    for classes in stats_state.get("scene_dataset_classes", {}).values():
        for cls_name in classes:
            scenes_by_class[cls_name] += 1
    # sploty, które w ogóle mają jakieś obrazy (do wykrycia "brak klasy w niepustym splicie")
    non_empty_splits = [
        split for split in SPLITS if stats_state["split_stats"][split]["images"] > 0
    ]
    total_dataset = sum(int(v) for v in dataset_counts.values())
    largest = max((int(v) for v in dataset_counts.values()), default=0)
    all_class_ids = sorted(set(class_names) | set(source_counts) | set(dataset_counts))

    results = []
    for cls_id in all_class_ids:
        name = class_names.get(cls_id, str(cls_id))
        dataset_count = int(dataset_counts.get(cls_id, 0))
        source_count = int(source_counts.get(cls_id, 0))
        train = int(split_class_counts["train"].get(name, 0))
        val = int(split_class_counts["val"].get(name, 0))
        test = int(split_class_counts["test"].get(name, 0))
        split_total = train + val + test
        missing = [
            split
            for split, count in (("train", train), ("val", val), ("test", test))
            if split in non_empty_splits and count == 0 and dataset_count > 0
        ]
        results.append(
            ClassDatasetStats(
                class_id=cls_id,
                name=name,
                source_annotations=source_count,
                dataset_annotations=dataset_count,
                train=train,
                val=val,
                test=test,
                used=dataset_count > 0,
                source_only=source_count > 0 and dataset_count == 0,
                tiles=int(tiles_by_class.get(name, 0)),
                scenes=int(scenes_by_class.get(name, 0)),
                share_pct=round(100.0 * dataset_count / total_dataset, 2) if total_dataset else 0.0,
                rel_to_largest=round(dataset_count / largest, 4) if largest else 0.0,
                train_pct=round(100.0 * train / split_total, 1) if split_total else 0.0,
                val_pct=round(100.0 * val / split_total, 1) if split_total else 0.0,
                test_pct=round(100.0 * test / split_total, 1) if split_total else 0.0,
                missing_in_splits=missing,
            )
        )
    return results


def _geom_median(values: list[float]) -> float:
    import numpy as np

    return round(float(np.median(values)), 3) if values else 0.0


def _geom_histogram(values: list[float], nbins: int, rng: tuple[float, float] | None = None) -> GeometryHistogram:
    import numpy as np

    if not values:
        return GeometryHistogram()
    arr = np.asarray(values, dtype=float)
    if rng is None:
        hi = float(np.percentile(arr, 99))
        if hi <= 0:
            hi = float(arr.max()) or 1.0
        lo = 0.0
    else:
        lo, hi = rng
    counts, edges = np.histogram(arr, bins=nbins, range=(lo, hi))
    return GeometryHistogram(
        bins=[round(float(e), 3) for e in edges],
        counts=[int(c) for c in counts],
    )


def _build_geometry_stats(
    selected_tiles: list[TileInfo],
    tile_annotations: dict[str, list[list[float]]],
    class_names: dict[int, str],
    tile_size: int,
    gsd_by_scene: dict[str, float | None],
    source_annotations_by_scene: dict[str, list[dict]],
) -> GeometryStats | None:
    """Rozmiary geometrii. AABB (w/h/pole/aspect/kubełki/metry) z tile_annotations (per-kafel);
    OBB (kąt/boki/near-square) z adnotacji źródłowych rotated_bbox (niezmienniczych przy kaflowaniu)."""
    try:
        import numpy as np  # noqa: F401
    except Exception:
        return None

    small_max = 32.0 * 32.0
    medium_max = 96.0 * 96.0

    def _aabb_acc() -> dict:
        return {"w": [], "h": [], "area": [], "frac": [], "aspect": [],
                "w_m": [], "h_m": [], "area_m2": [], "small": 0, "medium": 0, "large": 0}

    per: dict[str, dict] = defaultdict(_aabb_acc)
    all_area: list[float] = []
    all_aspect: list[float] = []
    total = 0
    covered = 0
    for tile in selected_tiles:
        anns = tile_annotations.get(tile.filename) or []
        gsd = gsd_by_scene.get(_scene_id_from_tile(tile))
        for ann in anns:
            if len(ann) < 5:
                continue
            w = float(ann[3]) * tile_size
            h = float(ann[4]) * tile_size
            if w <= 0 or h <= 0:
                continue
            area = w * h
            aspect = w / max(h, 1e-6)
            cls_name = class_names.get(int(ann[0]), str(int(ann[0])))
            d = per[cls_name]
            d["w"].append(w); d["h"].append(h); d["area"].append(area)
            d["frac"].append(float(ann[3]) * float(ann[4])); d["aspect"].append(aspect)
            all_area.append(area); all_aspect.append(min(aspect, 6.0))
            if area < small_max:
                d["small"] += 1
            elif area < medium_max:
                d["medium"] += 1
            else:
                d["large"] += 1
            total += 1
            if gsd:
                covered += 1
                d["w_m"].append(w * gsd); d["h_m"].append(h * gsd); d["area_m2"].append(area * gsd * gsd)

    if total == 0:
        return None

    # OBB z adnotacji źródłowych (rotated_bbox): kąt i boki niezmiennicze przy kaflowaniu.
    obb: dict[str, dict] = defaultdict(lambda: {"angle": [], "short": [], "long": [], "near": 0})
    all_angle: list[float] = []
    mode = "bbox"
    for anns in (source_annotations_by_scene or {}).values():
        for a in anns:
            if a.get("is_negative"):
                continue
            rb = a.get("rotated_bbox")
            if not rb:
                continue
            w = float(rb.get("width", 0) or 0)
            h = float(rb.get("height", 0) or 0)
            if w <= 0 or h <= 0:
                continue
            mode = "rotated_bbox"
            ang = float(rb.get("angle_deg", 0) or 0) % 180.0
            long_side = max(w, h)
            short_side = min(w, h)
            cls_name = class_names.get(int(a.get("class_id", -1)), str(a.get("class_id")))
            o = obb[cls_name]
            o["angle"].append(ang); o["short"].append(short_side); o["long"].append(long_side)
            if long_side > 0 and (long_side - short_side) / long_side < 0.1:
                o["near"] += 1
            all_angle.append(ang)

    name_to_id = {name: cid for cid, name in class_names.items()}
    per_class: list[GeometryClassStats] = []
    for name in sorted(set(per) | set(obb)):
        d = per.get(name)
        o = obb.get(name)
        gc = GeometryClassStats(class_id=name_to_id.get(name, -1), name=name)
        if d:
            gc.count = len(d["area"])
            gc.w_px_median = _geom_median(d["w"]); gc.h_px_median = _geom_median(d["h"])
            gc.area_px_median = _geom_median(d["area"])
            gc.area_px_p90 = round(float(np.percentile(d["area"], 90)), 3) if d["area"] else 0.0
            gc.area_frac_median = round(_geom_median(d["frac"]), 5)
            gc.aspect_median = _geom_median(d["aspect"])
            gc.size_small = d["small"]; gc.size_medium = d["medium"]; gc.size_large = d["large"]
            if d["area_m2"]:
                gc.w_m_median = _geom_median(d["w_m"]); gc.h_m_median = _geom_median(d["h_m"])
                gc.area_m2_median = _geom_median(d["area_m2"])
        if o:
            gc.obb_count = len(o["angle"]); gc.angle_median = _geom_median(o["angle"])
            gc.short_side_median = _geom_median(o["short"]); gc.long_side_median = _geom_median(o["long"])
            gc.near_square_count = o["near"]
        per_class.append(gc)

    return GeometryStats(
        mode=mode,
        tile_size=tile_size,
        size_thresholds_px={"small_max": small_max, "medium_max": medium_max},
        gsd_coverage_frac=round(covered / total, 4) if total else 0.0,
        total_annotations=total,
        per_class=per_class,
        area_px_hist=_geom_histogram(all_area, 24),
        aspect_hist=_geom_histogram(all_aspect, 24, (0.0, 6.0)),
        angle_hist=_geom_histogram(all_angle, 18, (0.0, 180.0)) if all_angle else None,
    )


def _season_from_iso(dt: str) -> str | None:
    try:
        month = int(str(dt)[5:7])
    except (ValueError, TypeError, IndexError):
        return None
    if month in (12, 1, 2):
        return "winter"
    if month in (3, 4, 5):
        return "spring"
    if month in (6, 7, 8):
        return "summer"
    if month in (9, 10, 11):
        return "autumn"
    return None


def _split_histogram(all_vals: list[float], split_vals: dict[str, list[float]], nbins: int) -> SplitHistogram | None:
    import numpy as np

    if not all_vals:
        return None
    arr = np.asarray(all_vals, dtype=float)
    lo = float(arr.min())
    hi = float(arr.max())
    if hi <= lo:
        hi = lo + 1.0
    edges = np.histogram_bin_edges(arr, bins=nbins, range=(lo, hi))

    def _h(vals: list[float]) -> list[int]:
        if not vals:
            return [0] * (len(edges) - 1)
        counts, _ = np.histogram(np.asarray(vals, dtype=float), bins=edges)
        return [int(c) for c in counts]

    return SplitHistogram(
        bins=[round(float(e), 4) for e in edges],
        counts=_h(all_vals),
        train=_h(split_vals.get("train", [])),
        val=_h(split_vals.get("val", [])),
        test=_h(split_vals.get("test", [])),
    )


def _build_acquisition_stats(
    selected_tiles: list[TileInfo],
    splits: dict[str, str],
    scene_context: dict[str, dict],
) -> AcquisitionStats | None:
    """Rozkłady metadanych akwizycji (GSD/sensor/modality/sezon/SAR incidence), ważone liczbą kafli,
    z rozbiciem per split. Wizualizacja (nie powiela ostrzeżeń audytu). None, gdy brak kontekstu scen."""
    try:
        import numpy as np  # noqa: F401
    except Exception:
        return None
    if not scene_context:
        return None

    gsd_all: list[float] = []
    gsd_split: dict[str, list[float]] = defaultdict(list)
    inc_all: list[float] = []
    inc_split: dict[str, list[float]] = defaultdict(list)
    sensor = MetaDist()
    modality = MetaDist()
    season = MetaDist()
    missing = {"gsd": 0, "sensor": 0, "date": 0}
    total = 0

    def _bump(dist: MetaDist, split: str, key: str) -> None:
        dist.overall[key] = dist.overall.get(key, 0) + 1
        bucket = getattr(dist, split, None)
        if isinstance(bucket, dict):
            bucket[key] = bucket.get(key, 0) + 1

    for tile in selected_tiles:
        split = splits.get(tile.filename, "train")
        if split not in ("train", "val", "test"):
            split = "train"
        ctx = scene_context.get(_scene_id_from_tile(tile)) or {}
        total += 1

        gsd = ctx.get("gsd_m")
        if isinstance(gsd, (int, float)) and gsd > 0:
            gsd_all.append(float(gsd)); gsd_split[split].append(float(gsd))
        else:
            missing["gsd"] += 1

        inc = ctx.get("incidence_angle_deg")
        if isinstance(inc, (int, float)):
            inc_all.append(float(inc)); inc_split[split].append(float(inc))

        s = ctx.get("sensor")
        if s:
            _bump(sensor, split, str(s))
        else:
            missing["sensor"] += 1

        m = ctx.get("modality")
        if m:
            _bump(modality, split, str(m))

        seas = _season_from_iso(ctx.get("acquisition_datetime_utc"))
        if seas:
            _bump(season, split, seas)
        else:
            missing["date"] += 1

    if total == 0:
        return None
    return AcquisitionStats(
        weighting="by_tile",
        total_tiles=total,
        gsd_hist=_split_histogram(gsd_all, gsd_split, 16),
        incidence_hist=_split_histogram(inc_all, inc_split, 16),
        sensor=sensor,
        modality=modality,
        season=season,
        missing=missing,
    )


def _build_co_occurrence(stats_state: dict, top_k: int = 40) -> CoOccurrence | None:
    """Macierz class × class: #kafli, na których obie klasy współwystępują (diag = #kafli z klasą).
    Przy dużej liczbie klas ograniczamy do Top-K najczęstszych (truncated=True)."""
    tiles_by_class: dict[str, int] = stats_state.get("tiles_by_class", {})
    present = [(name, cnt) for name, cnt in tiles_by_class.items() if cnt > 0]
    if len(present) < 2:
        return None
    present.sort(key=lambda kv: (-kv[1], kv[0]))
    truncated = len(present) > top_k
    chosen = [name for name, _ in present[:top_k]]
    index = {name: i for i, name in enumerate(chosen)}
    n = len(chosen)
    counts = [[0] * n for _ in range(n)]
    for i, name in enumerate(chosen):
        counts[i][i] = int(tiles_by_class.get(name, 0))
    for (a, b), c in (stats_state.get("co_pairs", {}) or {}).items():
        ia = index.get(a)
        ib = index.get(b)
        if ia is None or ib is None:
            continue
        counts[ia][ib] = int(c)
        counts[ib][ia] = int(c)
    return CoOccurrence(
        classes=chosen,
        counts=counts,
        tiles_total=int(stats_state.get("co_tiles_total", 0)),
        truncated=truncated,
    )


def _build_split_stats(stats_state: dict) -> list[SplitDatasetStats]:
    results = []
    for split in SPLITS:
        data = stats_state["split_stats"][split]
        results.append(
            SplitDatasetStats(
                split=split,
                images=int(data["images"]),
                positive_tiles=int(data["positive_tiles"]),
                negative_tiles=int(data["negative_tiles"]),
                annotations=int(data["annotations"]),
                per_class=dict(data["per_class"]),
            )
        )
    return results


def _build_scene_stats(
    tiles: list[TileInfo],
    selected_names: set[str],
    tile_annotations: dict[str, list[list[float]]],
    scene_context: dict[str, dict],
    source_scene_counts: dict[str, int],
    source_scene_classes: dict[str, set[str]],
    class_names: dict[int, str],
) -> list[SceneDatasetStats]:
    scene_tiles: dict[str, list[TileInfo]] = defaultdict(list)
    for tile in tiles:
        scene_tiles[_scene_id_from_tile(tile)].append(tile)

    scene_ids = sorted(set(scene_context) | set(scene_tiles))
    results = []
    for scene_id in scene_ids:
        tiles_for_scene = scene_tiles.get(scene_id, [])
        used_tiles = [tile for tile in tiles_for_scene if tile.filename in selected_names]
        dataset_classes = set()
        for tile in used_tiles:
            for ann in tile_annotations.get(tile.filename, []):
                cls_id = int(ann[0])
                dataset_classes.add(class_names.get(cls_id, str(cls_id)))

        context = scene_context.get(scene_id, {})
        filename = context.get("filename", scene_id)
        source_count = int(source_scene_counts.get(scene_id, 0))
        results.append(
            SceneDatasetStats(
                scene_id=scene_id,
                filename=filename,
                status=context.get("status", ""),
                tiles=len(tiles_for_scene),
                reviewed_tiles=sum(
                    1
                    for tile in tiles_for_scene
                    if not _is_excluded_tile(tile) and _is_reviewed_tile(tile)
                ),
                used_tiles=len(used_tiles),
                excluded_tiles=sum(1 for tile in tiles_for_scene if _is_excluded_tile(tile)),
                annotations=source_count,
                classes=sorted(source_scene_classes.get(scene_id, set())),
                without_annotations=source_count == 0,
                without_dataset_classes=len(dataset_classes) == 0,
            )
        )
    return results


def _is_reviewed_tile(tile: TileInfo) -> bool:
    return getattr(tile, "review_status", None) == "reviewed" or bool(getattr(tile, "reviewed", False))


def _is_excluded_tile(tile: TileInfo) -> bool:
    return bool(getattr(tile, "exclude_from_dataset", False) or getattr(tile, "excluded", False))
