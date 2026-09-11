"""Perceptual similarity scan shared by split validation and dataset audit."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from PIL import Image


ALGORITHM = "64-bit-dhash-lsh"
DEFAULT_HAMMING_THRESHOLD = 5
MAX_BUCKET_CANDIDATES = 128
MAX_REPORTED_PAIRS = 100


def scan_cross_split_near_duplicates(
    dataset_dir: str | Path,
    *,
    hamming_threshold: int = DEFAULT_HAMMING_THRESHOLD,
) -> dict[str, Any]:
    root = Path(dataset_dir)
    records: list[tuple[str, str, int]] = []
    failures: list[str] = []
    for split in ("train", "val", "test"):
        images_dir = root / split / "images"
        if not images_dir.exists():
            continue
        for path in sorted(item for item in images_dir.iterdir() if item.is_file()):
            try:
                records.append((split, path.name, difference_hash(path)))
            except (OSError, ValueError) as exc:
                failures.append(f"{split}/{path.name}: {exc}")

    buckets: dict[tuple[int, int], list[int]] = defaultdict(list)
    candidate_pairs: set[tuple[int, int]] = set()
    candidate_scan_truncated = False
    for index, (_split, _name, hash_value) in enumerate(records):
        for band in range(4):
            key = (band, (hash_value >> (band * 16)) & 0xFFFF)
            previous_items = buckets[key]
            if len(previous_items) > MAX_BUCKET_CANDIDATES:
                candidate_scan_truncated = True
            for previous in previous_items[-MAX_BUCKET_CANDIDATES:]:
                candidate_pairs.add((previous, index))
            previous_items.append(index)

    duplicates = []
    for left_index, right_index in candidate_pairs:
        left_split, left_name, left_hash = records[left_index]
        right_split, right_name, right_hash = records[right_index]
        if left_split == right_split:
            continue
        distance = (left_hash ^ right_hash).bit_count()
        if distance <= hamming_threshold:
            duplicates.append({
                "left": f"{left_split}/{left_name}",
                "right": f"{right_split}/{right_name}",
                "hamming_distance": distance,
            })
    duplicates.sort(key=lambda item: (item["hamming_distance"], item["left"], item["right"]))
    return {
        "algorithm": ALGORITHM,
        "hamming_threshold": hamming_threshold,
        "tile_count": len(records),
        "candidate_pair_count": len(candidate_pairs),
        "cross_split_pair_count": len(duplicates),
        "pairs": duplicates[:MAX_REPORTED_PAIRS],
        "reported_pair_limit": MAX_REPORTED_PAIRS,
        "scan_failure_count": len(failures),
        "scan_failures": failures[:MAX_REPORTED_PAIRS],
        "candidate_scan_truncated": candidate_scan_truncated,
        "status": "error" if duplicates else "warning" if failures or candidate_scan_truncated else "ok",
    }


def difference_hash(path: Path) -> int:
    with Image.open(path) as image:
        pixels = list(
            image.convert("L")
            .resize((9, 8), Image.Resampling.LANCZOS)
            .getdata()
        )
    value = 0
    for row in range(8):
        for column in range(8):
            value = (value << 1) | int(
                pixels[row * 9 + column] > pixels[row * 9 + column + 1]
            )
    return value


def pair_descriptions(summary: dict[str, Any]) -> list[str]:
    return [
        f"{item['left']} <-> {item['right']} (d={item['hamming_distance']})"
        for item in summary.get("pairs") or []
    ]
