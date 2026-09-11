"""On-hardware verification of SB1 (multimask + scores) and ST1 (SAM3 text mode).

Runs the real SAM backend against a synthetic image with distinct objects and reports
whether the ultralytics API actually delivers what SB1/ST1 rely on:

  SB1: does `multimask_output=True` return several masks, and do per-mask scores come
       through `result.boxes.conf`? (If not, SB2 degrades to size/compactness heuristic.)
  ST1: does the SAM3 predictor accept `texts=` and return instances + confidences?

Usage (packed env):
  MODELS_ROOT=... TORCHDYNAMO_DISABLE=1 KMP_DUPLICATE_LIB_OK=TRUE \
    .desktop-build/backend-env/python.exe scripts/verify-sam-real.py [checkpoint.pt]

Exit code 0 = both mechanisms verified (or cleanly degraded with a clear reason).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("TORCHDYNAMO_DISABLE", "1")
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "backend"))

import numpy as np  # noqa: E402


def synthetic_scene(size: int = 640) -> tuple[np.ndarray, tuple[int, int]]:
    """Gray background with a few bright rectangles ('vehicles') and one clear target.

    Returns the RGB image and the click point (centre of the target rectangle)."""
    import cv2

    rng = np.random.default_rng(7)
    img = (np.full((size, size, 3), 70, dtype=np.uint8)
           + rng.integers(-8, 8, (size, size, 3)).astype(np.int16)).clip(0, 255).astype(np.uint8)
    # Neighbouring rectangles (a small cluster) + one isolated target.
    boxes = [(300, 300, 40, 22), (352, 300, 40, 22), (300, 330, 40, 22),  # cluster
             (140, 470, 46, 26)]  # isolated target
    for (cx, cy, w, h) in boxes:
        cv2.rectangle(img, (cx - w // 2, cy - h // 2), (cx + w // 2, cy + h // 2), (210, 205, 195), -1)
        cv2.rectangle(img, (cx - w // 2, cy - h // 2), (cx + w // 2, cy + h // 2), (40, 40, 40), 1)
    return img, (300, 300)  # click on the cluster's first rectangle (stress the group bias)


def resolve_checkpoint(explicit: str | None) -> str:
    from services.sam_assist import resolve_sam_checkpoint

    if explicit:
        return explicit
    ckpt = resolve_sam_checkpoint(None)
    if not ckpt:
        raise SystemExit("No SAM checkpoint found in MODELS_ROOT/sam. Pass one as argv[1].")
    return ckpt


def main() -> int:
    from services.sam_assist import get_backend, select_object_mask

    checkpoint = resolve_checkpoint(sys.argv[1] if len(sys.argv) > 1 else None)
    print(f"[setup] checkpoint = {checkpoint}")
    rgb, click = synthetic_scene()
    backend = get_backend(checkpoint, "cpu")
    print(f"[setup] backend = {type(backend).__name__}, click = {click}")

    ok = True

    # --- SB1: multimask + scores ------------------------------------------------
    print("\n=== SB1: decode_masks (multimask + scores) ===")
    try:
        state = backend.encode(rgb)
        masks, scores = backend.decode_masks(state, [[float(click[0]), float(click[1])]], [])
        n = int(masks.shape[0])
        finite = np.isfinite(np.asarray(scores, dtype=float))
        print(f"  masks returned : {n}")
        print(f"  scores         : {np.asarray(scores).round(4).tolist()}")
        print(f"  scores finite  : {bool(finite.any())} ({int(finite.sum())}/{n})")
        multimask = n > 1
        print(f"  multimask works: {multimask}  (SB2 needs >1 to pick object vs group)")
        areas = masks.reshape(n, -1).sum(1) if n else np.array([])
        if n:
            legacy = int(np.argmax(areas))
            chosen = select_object_mask(masks, scores, (float(click[0]), float(click[1])), expected_area_px=46 * 26)
            print(f"  argmax(area) idx={legacy} area={int(areas[legacy])}  ->  SB2 idx={chosen} area={int(areas[chosen])}")
            print(f"  SB2 differs from legacy: {legacy != chosen}")
        if not multimask:
            print("  NOTE: single mask only -> SB2 still safe, but the object/part/subpart"
                  " lever is unavailable in this runtime.")
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"  SB1 FAILED: {type(exc).__name__}: {exc}")

    # --- ST1: text-prompted segmentation ---------------------------------------
    # Optional real image + class word: `verify-sam-real.py <ckpt> <image> <word>`.
    text_rgb, text_word = rgb, "vehicle"
    if len(sys.argv) > 2 and Path(sys.argv[2]).is_file():
        from PIL import Image

        text_word = sys.argv[3] if len(sys.argv) > 3 else "building"
        im = Image.open(sys.argv[2]).convert("RGB")
        w, h = im.size
        side = min(1024, w, h)
        left, top = (w - side) // 2, (h - side) // 2
        text_rgb = np.asarray(im.crop((left, top, left + side, top + side)))
        print(f"\n[ST input] real image {Path(sys.argv[2]).name} crop {text_rgb.shape}, word='{text_word}'")
    print("\n=== ST1: text_masks (SAM3 text mode) ===")
    try:
        tmasks, tscores = backend.text_masks(text_rgb, [text_word])
        tn = int(tmasks.shape[0])
        print(f"  instances      : {tn}")
        print(f"  scores         : {np.asarray(tscores).round(4).tolist()}")
        print(f"  text mode works: True (API accepted the text prompt)")
        if tn == 0:
            print("  NOTE: 0 instances is fine for API verification — the path works without error.")
    except Exception as exc:  # noqa: BLE001
        from services.sam_assist import SamAssistError

        if isinstance(exc, SamAssistError):
            print(f"  text mode unavailable (clean degradation): {exc}")
        else:
            ok = False
            print(f"  ST1 FAILED (unexpected): {type(exc).__name__}: {exc}")

    print("\n[result]", "OK" if ok else "FAILURES — see above")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
