"""Audyt rozciagniecia tonalnego kafli na prawdziwej scenie (DESIGN_DECISIONS.md, display-stretch F0).

Tylko odczyt: histogram liczony od nowa w pamieci, nic nie trafia do projektu. Te same okna
sceny przechodza przez kilka wariantow renderu i dla kazdego liczone sa miary z planu:

- ``odcienie``  - liczba uzywanych poziomow w srodkowych 90% jasnosci kafla (mediana po kaflach),
- ``przerwa``   - najwiekszy odstep miedzy uzywanymi poziomami (mediana po kaflach),
- ``czern`` / ``biel`` - odsetek pikseli 0 i 255,
- ``cele_biel`` - odsetek jasnych celow (piksele powyzej p99 sceny) wypalonych do 255,
- ``szew``      - skok sredniej jasnosci na granicy kafli / ta sama miara w srodku kafla.

Warianty:

- ``przed_55b9947``  - dla scen z oknem wyswietlania nastawa byla "procentem z 255" (no-op),
- ``kwantyzacja_2x`` - progi sceny nalozone na obraz JUZ skwantowany do uint8 (stan przed A),
- ``renderer``       - to, co faktycznie wola kod kafli (``utils.image.render_display_window``,
                       poprawka A: okno sceny + regulacje tonalne, jedna kwantyzacja),
- ``kafel_u8`` / ``kafel_float`` - percentyle liczone na kazdym kaflu (odrzucone w planie),
- ``widok``          - progi z calego badanego okna (zakres "Widok", koncepcja D),
- ``sar_domyslne``   - 1-99,8% bez gammy i kontrastu (poprawki B+C).

Przyklad:

    python backend/benchmarks/display_stretch_audit.py --project-id <project-id> \
        --scene-id <scene-id> --images-dir benchmark-results/stretch
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from benchmarks.common import add_common_arguments, configure_environment, output_path  # noqa: E402

import numpy as np  # noqa: E402

OPERATION = "display_stretch_audit"
TILE = 256
SAR_DEFAULT_STRETCH = (1.0, 99.8)
DEFAULT_REGIONS = {
    # nazwa: (srodek x, srodek y jako ulamek sceny, bok okna w px zrodla, rozmiar wyjscia)
    "srodek_1x": (0.5, 0.5, 1024, 1024),
    "srodek_4x": (0.5, 0.5, 4096, 1024),
    "cala_scena": (0.5, 0.5, 0, 1024),  # bok 0 = krotszy bok sceny
}


@dataclass
class SceneInput:
    project_id: str
    scene_id: str
    path: Path
    dtype: str
    display_min: float | None
    display_max: float | None
    display_mode: str | None
    brightness: float
    contrast: float
    gamma: float


# --------------------------------------------------------------------------- wejscie
def load_scene_input(project_id: str, scene_id: str) -> SceneInput:
    from db.storage import load_json, load_scene_json
    from services.scene_raster_resolver import SceneRasterResolver

    scene = load_scene_json(project_id, scene_id, "scene", default={}) or {}
    si = scene.get("scene_info") or {}
    handle = SceneRasterResolver.resolve(project_id, scene_id)
    project = load_json(project_id, "project", default={}) or {}
    profile_id = (project.get("profile") or {}).get("default_preprocessing_profile")
    profiles = (load_json(project_id, "preprocessing_profiles", default={}) or {}).get("profiles", [])
    profile = next((p for p in profiles if p.get("profile_id") == profile_id), {})
    return SceneInput(
        project_id=project_id,
        scene_id=scene_id,
        path=Path(handle.path),
        dtype=str(si.get("dtype") or ""),
        display_min=si.get("display_min"),
        display_max=si.get("display_max"),
        display_mode=si.get("display_mode"),
        brightness=float(profile.get("brightness", 1.0)),
        contrast=float(profile.get("contrast", 1.0)),
        gamma=float(profile.get("gamma", 1.0)),
    )


def valid_mask(arr: np.ndarray, nodata) -> np.ndarray:
    finite = np.isfinite(arr)
    if nodata is None or (isinstance(nodata, float) and np.isnan(nodata)):
        return finite & (arr != 0)
    return finite & (arr != nodata)


# --------------------------------------------------------------------------- warianty
def display_domain(arr: np.ndarray, scene: SceneInput) -> np.ndarray:
    from utils.image import _apply_display_mode

    return _apply_display_mode(arr, scene.display_mode)


def window_to_legacy_255(value: float, scene: SceneInput) -> float:
    """Przeniesienie progu do 0-255 okna bazowego — tak liczyl kod przed F1."""
    if scene.display_min is None or scene.display_max is None:
        return float(np.clip(value, 0.0, 255.0))
    scaled = (value - scene.display_min) / (scene.display_max - scene.display_min) * 255.0
    return float(np.clip(scaled, 0.0, 255.0))


def build_variants(scene: SceneInput, histogram: dict, stretch: tuple[float, float]):
    from services.image_preprocessor import apply_display_params
    from services.scene_histogram import percentile_to_value
    from utils.image import ensure_rgb_uint8, render_display_window

    lp, hp = stretch
    low = percentile_to_value(histogram, lp)
    high = percentile_to_value(histogram, hp)
    sar_low = percentile_to_value(histogram, SAR_DEFAULT_STRETCH[0])
    sar_high = percentile_to_value(histogram, SAR_DEFAULT_STRETCH[1])
    tonal = dict(brightness=scene.brightness, contrast=scene.contrast, gamma=scene.gamma)
    dmin, dmax, mode = scene.display_min, scene.display_max, scene.display_mode

    def base(arr):
        return ensure_rgb_uint8(arr, dmin, dmax, mode)[..., 0]

    def legacy(arr, _valid):
        return apply_display_params(base(arr), stretch_low=lp, stretch_high=hp,
                                    percentile_stretch=dmin is None, **tonal)

    def double_quantization(arr, _valid):
        bounds = (window_to_legacy_255(low, scene), window_to_legacy_255(high, scene))
        return apply_display_params(base(arr), stretch_bounds=bounds, **tonal)

    def rendered(arr, window, with_tonal=True):
        return render_display_window(arr, mode, window, **(tonal if with_tonal else {}))[..., 0]

    def renderer(arr, _valid):
        return rendered(arr, (low, high))

    def per_tile(fn):
        def run(arr, valid):
            out = np.zeros(arr.shape[:2], dtype=np.uint8)
            for ty in range(0, arr.shape[0], TILE):
                for tx in range(0, arr.shape[1], TILE):
                    sl = (slice(ty, ty + TILE), slice(tx, tx + TILE))
                    out[sl] = fn(arr[sl], valid[sl])
            return out
        return run

    def tile_u8(arr, _valid):
        tile = base(arr)
        return apply_display_params(tile, stretch_low=lp, stretch_high=hp, **tonal)

    def tile_float(arr, valid):
        values = display_domain(arr, scene)
        if valid.sum() < 16:
            return np.zeros(arr.shape[:2], np.uint8)
        lo, hi = np.percentile(values[valid], [lp, hp])
        if not hi > lo:
            return np.zeros(arr.shape[:2], np.uint8)
        return rendered(arr, (lo, hi))

    def view(arr, valid):
        values = display_domain(arr, scene)
        lo, hi = np.percentile(values[valid], [lp, hp])
        return rendered(arr, (lo, hi))

    def sar_default(arr, _valid):
        return rendered(arr, (sar_low, sar_high), with_tonal=False)

    variants: dict[str, Callable] = {"przed_55b9947": legacy}
    if dmin is not None:
        variants["kwantyzacja_2x"] = double_quantization
    variants["renderer"] = renderer
    variants["kafel_u8"] = per_tile(tile_u8)
    variants["kafel_float"] = per_tile(tile_float)
    variants["widok"] = view
    variants["sar_domyslne"] = sar_default
    return base, variants


# --------------------------------------------------------------------------- miary
def level_stats(u8: np.ndarray, valid: np.ndarray) -> tuple[int, int]:
    values = u8[valid]
    if values.size == 0:
        return 0, 0
    occupied = np.flatnonzero(np.bincount(values.ravel(), minlength=256))
    lo, hi = np.percentile(values, [5, 95])
    core = occupied[(occupied >= lo) & (occupied <= hi)]
    gap = int(np.max(np.diff(core))) if core.size > 1 else 0
    return int(core.size), gap


def tile_level_stats(u8: np.ndarray, valid: np.ndarray) -> tuple[int | None, int | None]:
    cores, gaps = [], []
    for ty in range(0, u8.shape[0], TILE):
        for tx in range(0, u8.shape[1], TILE):
            sl = (slice(ty, ty + TILE), slice(tx, tx + TILE))
            if valid[sl].mean() < 0.95:
                continue
            core, gap = level_stats(u8[sl], valid[sl])
            cores.append(core)
            gaps.append(gap)
    if not cores:
        return None, None
    return int(np.median(cores)), int(np.median(gaps))


def seam_ratio(u8: np.ndarray, valid: np.ndarray, strip: int = 12) -> tuple[float, float]:
    img = u8.astype(np.float32)

    def steps(offset: int) -> list[float]:
        found = []
        h, w = img.shape
        for b in range(offset, w - strip, TILE):
            if b < strip:
                continue
            for ty in range(0, h, TILE):
                ys = slice(ty, min(h, ty + TILE))
                vl, vr = valid[ys, b - strip:b], valid[ys, b:b + strip]
                if vl.mean() > 0.9 and vr.mean() > 0.9:
                    found.append(abs(img[ys, b - strip:b][vl].mean() - img[ys, b:b + strip][vr].mean()))
        for b in range(offset, h - strip, TILE):
            if b < strip:
                continue
            for tx in range(0, w, TILE):
                xs = slice(tx, min(w, tx + TILE))
                vt, vb = valid[b - strip:b, xs], valid[b:b + strip, xs]
                if vt.mean() > 0.9 and vb.mean() > 0.9:
                    found.append(abs(img[b - strip:b, xs][vt].mean() - img[b:b + strip, xs][vb].mean()))
        return found

    border, inner = steps(TILE), steps(TILE // 2)
    return (round(float(np.mean(border)), 2) if border else float("nan"),
            round(float(np.mean(inner)), 2) if inner else float("nan"))


def measure(u8: np.ndarray, valid: np.ndarray, targets: np.ndarray) -> dict:
    values = u8[valid]
    tiles_core, tiles_gap = tile_level_stats(u8, valid)
    border, inner = seam_ratio(u8, valid)
    target_values = u8[targets]
    return {
        "odcienie": tiles_core,
        "przerwa": tiles_gap,
        "czern_pct": round(float(np.mean(values == 0)) * 100, 1),
        "biel_pct": round(float(np.mean(values == 255)) * 100, 1),
        "srednia": round(float(values.mean()), 1),
        "cele_biel_pct": round(float(np.mean(target_values == 255)) * 100, 1) if target_values.size else None,
        "szew_granica": border,
        "szew_wnetrze": inner,
    }


# --------------------------------------------------------------------------- przebieg
def parse_region(text: str) -> tuple[str, tuple[float, float, int, int]]:
    name, spec = text.split(":", 1)
    cx, cy, side, out_px = spec.split(",")
    return name, (float(cx), float(cy), int(side), int(out_px))


def run_audit(scene: SceneInput, regions: dict, stretch: tuple[float, float],
              images_dir: Path | None = None) -> dict:
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.windows import Window

    from services.scene_histogram import compute_raster_histogram

    histogram = compute_raster_histogram(scene.path, display_mode=scene.display_mode)
    base, variants = build_variants(scene, histogram, stretch)
    report = {
        "scene": {"project_id": scene.project_id, "scene_id": scene.scene_id,
                  "dtype": scene.dtype, "display_mode": scene.display_mode,
                  "display_min": scene.display_min, "display_max": scene.display_max,
                  "tonal": {"brightness": scene.brightness, "contrast": scene.contrast,
                            "gamma": scene.gamma}},
        "stretch": list(stretch),
        "regions": {},
    }
    with rasterio.open(scene.path) as src:
        scale = min(1.0, (1_500_000 / (src.width * src.height)) ** 0.5)
        sample = src.read(1, out_shape=(max(1, int(src.height * scale)), max(1, int(src.width * scale))),
                          resampling=Resampling.nearest)
        sample_valid = valid_mask(sample, src.nodata)
        target_threshold = float(np.percentile(display_domain(sample, scene)[sample_valid], 99.0))
        for name, (cx, cy, side, out_px) in regions.items():
            side = side or min(src.width, src.height)
            col = max(0, int(cx * src.width - side / 2))
            row = max(0, int(cy * src.height - side / 2))
            arr = src.read(1, window=Window(col, row, side, side), out_shape=(out_px, out_px),
                           resampling=Resampling.nearest, boundless=True,
                           fill_value=src.nodata if src.nodata is not None else 0)
            valid = valid_mask(arr, src.nodata)
            targets = valid & (display_domain(arr, scene) >= target_threshold)
            rendered = {key: fn(arr, valid) for key, fn in variants.items()}
            report["regions"][name] = {
                "window": [col, row, side, side],
                "output_px": out_px,
                "valid_pct": round(float(valid.mean()) * 100, 1),
                "variants": {key: measure(img, valid, targets) for key, img in rendered.items()},
            }
            if images_dir is not None:
                save_panels(images_dir / f"{scene.scene_id}_{name}.png",
                            {"baza": base(arr), **rendered})
    return report


def save_panels(path: Path, panels: dict[str, np.ndarray]) -> None:
    from PIL import Image, ImageDraw

    path.parent.mkdir(parents=True, exist_ok=True)
    items = []
    for key, img in panels.items():
        im = Image.fromarray(img).convert("RGB")
        draw = ImageDraw.Draw(im)
        draw.rectangle([0, 0, 8 * len(key) + 10, 18], fill=(255, 255, 0))
        draw.text((5, 3), key, fill=(0, 0, 0))
        items.append(im)
    cols = 3
    w, h = items[0].size
    rows = (len(items) + cols - 1) // cols
    grid = Image.new("RGB", (cols * w + (cols - 1) * 6, rows * h + (rows - 1) * 6), (255, 0, 255))
    for i, im in enumerate(items):
        grid.paste(im, ((i % cols) * (w + 6), (i // cols) * (h + 6)))
    grid.save(path)


def print_report(report: dict) -> None:
    scene = report["scene"]
    print(f"{scene['project_id']}/{scene['scene_id']}  dtype={scene['dtype']} "
          f"mode={scene['display_mode']}  nastawa {report['stretch'][0]}-{report['stretch'][1]}%  "
          f"tonal={scene['tonal']}")
    header = f"  {'wariant':18} {'odcienie':>8} {'przerwa':>7} {'czern%':>7} {'biel%':>6} " \
             f"{'cele%':>6} {'srednia':>7} {'szew gr/wn':>12}"
    for name, region in report["regions"].items():
        print(f"\n  region {name}: okno {region['window']} -> {region['output_px']} px, "
              f"wazne {region['valid_pct']}%")
        print(header)
        for key, m in region["variants"].items():
            print(f"  {key:18} {str(m['odcienie']):>8} {str(m['przerwa']):>7} {m['czern_pct']:>7} "
                  f"{m['biel_pct']:>6} {str(m['cele_biel_pct']):>6} {m['srednia']:>7} "
                  f"{m['szew_granica']:>5} / {m['szew_wnetrze']:<5}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_common_arguments(parser, project_required=True)
    parser.add_argument("--scene-id", required=True)
    parser.add_argument("--stretch", default="2,98", help="Nastawa percentyli, np. 2,98")
    parser.add_argument("--region", action="append", default=[],
                        help="nazwa:cx,cy,bok,wyjscie (ulamki sceny, px); domyslnie trzy regiony")
    parser.add_argument("--tonal", default=None,
                        help="jasnosc,kontrast,gamma; domyslnie z domyslnego profilu projektu")
    parser.add_argument("--images-dir", help="Katalog na obrazy porownawcze (opcjonalnie)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.data_dir and os.environ.get("APPDATA"):
        default_data = Path(os.environ["APPDATA"]) / "GeoTileLabel" / "data"
        if default_data.is_dir():
            args.data_dir = str(default_data)
    configure_environment(args)
    scene = load_scene_input(args.project_id, args.scene_id)
    if args.tonal:
        scene.brightness, scene.contrast, scene.gamma = (float(v) for v in args.tonal.split(","))
    regions = dict(parse_region(text) for text in args.region) or DEFAULT_REGIONS
    stretch = tuple(float(v) for v in args.stretch.split(","))
    report = run_audit(scene, regions, stretch,
                       Path(args.images_dir).resolve() if args.images_dir else None)
    print_report(report)
    out = output_path(args, OPERATION)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nraport: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
