"""Exemplar propagation — "label one, find the rest" (EO, SAR).

A2 research-spike **floor**: classical normalized cross-correlation
(``cv2.matchTemplate``) of the exemplar patch over a neighbourhood, then NMS.
It is model-free and supports a local neighbourhood, the current map viewport,
or the whole scene. Large ranges are read in bounded overlapping windows, so
the source raster is never loaded into memory in full. Proposals flow through
the same staging as SAM / YOLO.

Limitations (documented, not hidden): single-scale, axis-aligned only, and
sensitive to rotation/appearance change — it is a first-pass accelerator the
user reviews, not a detector.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np

from services.sam_assist import Window, read_rgb_window

EXEMPLAR_REGION_PX = 2048  # neighbourhood searched around the exemplar
SEARCH_BLOCK_PX = 2048
DEFAULT_THRESHOLD = 0.70
DEFAULT_SCALE_TOLERANCE = 0.10
DEFAULT_ROTATION_TOLERANCE_DEG = 20
DEFAULT_USE_EDGES = True
DEFAULT_MAX_RESULTS = 200
SELF_IOU = 0.5  # drop matches that essentially coincide with the exemplar
COARSE_THRESHOLD_MARGIN = 0.15
EDGE_WEIGHT = 0.30
MAX_REFINEMENT_CANDIDATES = 240
REFINEMENT_MARGIN_FRACTION = 0.18


@dataclass(frozen=True)
class TemplateVariant:
    scale: float
    angle_deg: float
    gray: np.ndarray
    edges: np.ndarray


class ExemplarError(RuntimeError):
    pass


def _display_transfer(
    proposals: list[dict[str, Any]],
    *,
    exemplar_bbox: list[float],
    exemplar_polygon: list[list[float]],
    model: Any,
) -> list[dict[str, Any]]:
    """Odbuduj ramke kazdego dopasowania jako PROSTOKAT NA MAPIE.

    DLACZEGO NIE PRZELICZAC KATA DOPASOWANIA. Obrot o `match_angle_deg` jest obrotem
    w PIKSELACH, a na scenie niekonforemnej obrot pikselowy nie ma odpowiednika w postaci
    obrotu w przestrzeni wyswietlania — jest tam ogolnym odwzorowaniem liniowym. Kierunek
    przenosimy wiec jako PARE PUNKTOW (srodek i koniec obroconej krawedzi przodu), a boki
    bierzemy z dlugosci zmierzonych w przestrzeni wyswietlania. Prostokatnosc wychodzi
    wtedy z konstrukcji, a nie z przyblizenia.

    Zmierzone na scenie EPSG:4326 na szerokosci 60 stopni, szesc przypadkow (obrot
    0-90 stopni, przesuniecie do 2500 px, skala 1,5):

        wariant                          skos       blad boku
        A: prostokat pikselowy (dotad)   1,7-36,2   do 74%
        B: narozniki przez podobienstwo  0,0-58,1   do 31%
        D: przebudowa w wyswietlaniu     0,000      do 0,01%

    Wariant B — przeniesienie naroznikow tym samym podobienstwem pikselowym — wydawal sie
    naturalny, ale przy obrocie wypada GORZEJ niz stan dotychczasowy. Stad wybor D.
    """
    import math

    from services.sensor_geometry import display_to_pixel, pixel_to_display

    exemplar_centre_px = [
        (float(exemplar_bbox[0]) + float(exemplar_bbox[2])) / 2.0,
        (float(exemplar_bbox[1]) + float(exemplar_bbox[3])) / 2.0,
    ]
    obb_centre_px = [
        sum(float(point[0]) for point in exemplar_polygon[:4]) / 4.0,
        sum(float(point[1]) for point in exemplar_polygon[:4]) / 4.0,
    ]
    exemplar_display = pixel_to_display(model, [list(map(float, point)) for point in exemplar_polygon[:4]])
    source_width = math.dist(exemplar_display[0], exemplar_display[1])
    source_height = math.dist(exemplar_display[1], exemplar_display[2])
    if source_width <= 0 or source_height <= 0:
        return proposals

    for proposal in proposals:
        box = proposal.get("bbox") or []
        if len(box) != 4:
            continue
        match_scale = float(proposal.pop("_match_scale", 1.0))
        match_angle_deg = float(proposal.pop("_match_angle_deg", 0.0))
        rotation = math.radians(match_angle_deg)
        cos_r, sin_r = math.cos(rotation), math.sin(rotation)

        candidate_centre = [
            (float(box[0]) + float(box[2])) / 2.0,
            (float(box[1]) + float(box[3])) / 2.0,
        ]
        offset_x = (obb_centre_px[0] - exemplar_centre_px[0]) * match_scale
        offset_y = (obb_centre_px[1] - exemplar_centre_px[1]) * match_scale
        centre_px = [
            candidate_centre[0] + offset_x * cos_r - offset_y * sin_r,
            candidate_centre[1] + offset_x * sin_r + offset_y * cos_r,
        ]

        front_px = [
            float(exemplar_polygon[1][0]) - float(exemplar_polygon[0][0]),
            float(exemplar_polygon[1][1]) - float(exemplar_polygon[0][1]),
        ]
        rotated_front = [
            front_px[0] * cos_r - front_px[1] * sin_r,
            front_px[0] * sin_r + front_px[1] * cos_r,
        ]
        tip_px = [centre_px[0] + rotated_front[0], centre_px[1] + rotated_front[1]]

        try:
            centre_d, tip_d = pixel_to_display(model, [centre_px, tip_px])
        except Exception:  # noqa: BLE001 — pojedyncze dopasowanie nie moze przewrocic calosci
            continue
        ux, uy = tip_d[0] - centre_d[0], tip_d[1] - centre_d[1]
        norm = math.hypot(ux, uy)
        if norm <= 0:
            continue
        ux, uy = ux / norm, uy / norm
        nx, ny = -uy, ux
        half_w = source_width * match_scale / 2.0
        half_h = source_height * match_scale / 2.0
        corners_display = [
            [centre_d[0] - ux * half_w - nx * half_h, centre_d[1] - uy * half_w - ny * half_h],
            [centre_d[0] + ux * half_w - nx * half_h, centre_d[1] + uy * half_w - ny * half_h],
            [centre_d[0] + ux * half_w + nx * half_h, centre_d[1] + uy * half_w + ny * half_h],
            [centre_d[0] - ux * half_w + nx * half_h, centre_d[1] - uy * half_w + ny * half_h],
        ]
        try:
            polygon = [
                [round(float(x), 3), round(float(y), 3)]
                for x, y in display_to_pixel(model, corners_display)
            ]
        except Exception:  # noqa: BLE001
            continue

        from services.sam_assist import front_vector_from_polygon
        from services.sensor_geometry import rotated_bbox_from_polygon_px

        summary = rotated_bbox_from_polygon_px(polygon)
        if summary is None:
            continue
        proposal["geometry_type"] = "rotated_bbox"
        proposal["rotated_bbox"] = summary
        proposal["polygon_scene_px"] = polygon
        proposal["front_vector_scene_px"] = front_vector_from_polygon(polygon)
        proposal["bbox"] = [
            min(point[0] for point in polygon),
            min(point[1] for point in polygon),
            max(point[0] for point in polygon),
            max(point[1] for point in polygon),
        ]
    return proposals


def transfer_exemplar_geometry(
    proposals: list[dict[str, Any]],
    *,
    exemplar_bbox: list[float],
    geometry_type: str,
    exemplar_rotated_bbox: dict[str, float] | None,
    exemplar_front_vector: list[float] | None,
    exemplar_polygon_scene_px: list[list[float]] | None = None,
    project_id: str | None = None,
    scene_id: str | None = None,
) -> list[dict[str, Any]]:
    """Translate an exemplar OBB and its front direction to every match.

    Gdy scena ma model geo, a wzorzec niesie swoj poligon, ramki sa budowane od nowa
    w przestrzeni wyswietlania (`_display_transfer`) — tak jak propozycje SAM. Sciezka
    pikselowa ponizej zostaje dla scen bez geo i dla wzorcow bez poligonu (starsi klienci).
    """
    if geometry_type != "rotated_bbox" or not exemplar_rotated_bbox:
        return proposals

    if exemplar_polygon_scene_px and len(exemplar_polygon_scene_px) >= 4 and project_id and scene_id:
        try:
            from db.storage import load_scene_json
            from services.sensor_geometry import SceneGeoModel

            model = SceneGeoModel.from_manifest(
                load_scene_json(project_id, scene_id, "scene_manifest", default={})
            )
        except Exception:  # noqa: BLE001
            model = None
        if model is not None:
            return _display_transfer(
                proposals,
                exemplar_bbox=exemplar_bbox,
                exemplar_polygon=exemplar_polygon_scene_px,
                model=model,
            )

    import math

    ex_cx = (float(exemplar_bbox[0]) + float(exemplar_bbox[2])) / 2.0
    ex_cy = (float(exemplar_bbox[1]) + float(exemplar_bbox[3])) / 2.0
    source_cx = float(exemplar_rotated_bbox["cx"])
    source_cy = float(exemplar_rotated_bbox["cy"])
    source_width = float(exemplar_rotated_bbox["width"])
    source_height = float(exemplar_rotated_bbox["height"])
    source_angle_deg = float(exemplar_rotated_bbox["angle_deg"])

    front = exemplar_front_vector
    if front and len(front) >= 2:
        length = math.hypot(float(front[0]), float(front[1]))
        front = ([float(front[0]) / length, float(front[1]) / length]
                 if length > 1e-9 else None)
    if not front:
        from services.sam_assist import default_front_vector

        front = default_front_vector({
            "width": source_width,
            "height": source_height,
            "angle_deg": source_angle_deg,
        })

    for proposal in proposals:
        box = proposal.get("bbox") or []
        if len(box) != 4:
            continue
        match_scale = float(proposal.pop("_match_scale", 1.0))
        match_angle_deg = float(proposal.pop("_match_angle_deg", 0.0))
        candidate_cx = (float(box[0]) + float(box[2])) / 2.0
        candidate_cy = (float(box[1]) + float(box[3])) / 2.0
        rotation = math.radians(match_angle_deg)
        cos_rotation, sin_rotation = math.cos(rotation), math.sin(rotation)
        offset_x = (source_cx - ex_cx) * match_scale
        offset_y = (source_cy - ex_cy) * match_scale
        width = source_width * match_scale
        height = source_height * match_scale
        angle_deg = source_angle_deg + match_angle_deg
        rotated = {
            "cx": candidate_cx + offset_x * cos_rotation - offset_y * sin_rotation,
            "cy": candidate_cy + offset_x * sin_rotation + offset_y * cos_rotation,
            "width": width,
            "height": height,
            "angle_deg": angle_deg,
        }
        angle = math.radians(angle_deg)
        cos_a, sin_a = math.cos(angle), math.sin(angle)
        half_w, half_h = width / 2.0, height / 2.0
        corners = []
        for dx, dy in ((-half_w, -half_h), (half_w, -half_h),
                       (half_w, half_h), (-half_w, half_h)):
            corners.append((
                rotated["cx"] + dx * cos_a - dy * sin_a,
                rotated["cy"] + dx * sin_a + dy * cos_a,
            ))
        proposal["geometry_type"] = "rotated_bbox"
        proposal["rotated_bbox"] = rotated
        proposal["front_vector_scene_px"] = [
            float(front[0]) * cos_rotation - float(front[1]) * sin_rotation,
            float(front[0]) * sin_rotation + float(front[1]) * cos_rotation,
        ]
        proposal["bbox"] = [
            min(point[0] for point in corners),
            min(point[1] for point in corners),
            max(point[0] for point in corners),
            max(point[1] for point in corners),
        ]
    return proposals


def compute_region(exemplar_bbox: list[float], scene_w: int, scene_h: int) -> Window:
    """Search window centred on the exemplar, clamped to the scene."""
    cx = (exemplar_bbox[0] + exemplar_bbox[2]) / 2.0
    cy = (exemplar_bbox[1] + exemplar_bbox[3]) / 2.0
    side_w = min(EXEMPLAR_REGION_PX, scene_w)
    side_h = min(EXEMPLAR_REGION_PX, scene_h)
    x0 = int(round(cx)) - side_w // 2
    y0 = int(round(cy)) - side_h // 2
    x0 = max(0, min(x0, scene_w - side_w))
    y0 = max(0, min(y0, scene_h - side_h))
    return Window(x0, y0, side_w, side_h)


def resolve_search_region(
    search_mode: Literal["local", "viewport", "scene"],
    exemplar_bbox: list[float],
    scene_w: int,
    scene_h: int,
    search_bbox: list[float] | None = None,
) -> Window:
    if search_mode == "local":
        return compute_region(exemplar_bbox, scene_w, scene_h)
    if search_mode == "scene":
        return Window(0, 0, scene_w, scene_h)
    if search_mode != "viewport":
        raise ExemplarError(f"Unsupported search mode: {search_mode}")
    if search_bbox is None or len(search_bbox) != 4:
        raise ExemplarError("Visible-area search requires search_bbox")

    x0 = max(0, min(scene_w, int(math.floor(min(search_bbox[0], search_bbox[2])))))
    y0 = max(0, min(scene_h, int(math.floor(min(search_bbox[1], search_bbox[3])))))
    x1 = max(0, min(scene_w, int(math.ceil(max(search_bbox[0], search_bbox[2])))))
    y1 = max(0, min(scene_h, int(math.ceil(max(search_bbox[1], search_bbox[3])))))
    if x1 <= x0 or y1 <= y0:
        raise ExemplarError("The visible map area does not intersect the scene")
    return Window(x0, y0, x1 - x0, y1 - y0)


def iter_search_blocks(region: Window, template_w: int, template_h: int):
    """Yield windows whose valid template origins cover ``region`` exactly once."""
    block_w = max(SEARCH_BLOCK_PX, template_w + 1)
    block_h = max(SEARCH_BLOCK_PX, template_h + 1)
    step_x = max(1, block_w - template_w + 1)
    step_y = max(1, block_h - template_h + 1)
    max_x = region.x0 + region.width - template_w
    max_y = region.y0 + region.height - template_h
    y = region.y0
    while y <= max_y:
        x = region.x0
        height = min(block_h, region.y0 + region.height - y)
        while x <= max_x:
            width = min(block_w, region.x0 + region.width - x)
            yield Window(x, y, width, height)
            x += step_x
        y += step_y


def _iou(a: list[float], b: list[float]) -> float:
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0.0, ix1 - ix0), max(0.0, iy1 - iy0)
    inter = iw * ih
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    union = area_a + area_b - inter + 1e-9
    return inter / union


def _nms(boxes: list[list[float]], scores: list[float], iou_thr: float) -> list[int]:
    order = sorted(range(len(boxes)), key=lambda i: -scores[i])
    keep: list[int] = []
    while order:
        i = order.pop(0)
        keep.append(i)
        order = [j for j in order if _iou(boxes[i], boxes[j]) <= iou_thr]
    return keep


def _scale_factors(tolerance: float) -> list[float]:
    tolerance = max(0.0, min(float(tolerance), 0.20))
    if tolerance < 0.01:
        return [1.0]
    if tolerance <= 0.11:
        return [0.9, 1.0, 1.1]
    return [0.8, 0.9, 1.0, 1.1, 1.2]


def _rotation_angles(tolerance_deg: int) -> list[float]:
    tolerance = max(0, min(int(tolerance_deg), 45))
    if tolerance == 0:
        return [0.0]
    step = 10 if tolerance <= 20 else 15
    values = list(range(-tolerance, tolerance + 1, step))
    if 0 not in values:
        values.append(0)
    if tolerance not in values:
        values.extend([-tolerance, tolerance])
    return sorted({float(value) for value in values})


def _coarse_rotation_angles(tolerance_deg: int) -> list[float]:
    tolerance = max(0, min(int(tolerance_deg), 45))
    if tolerance == 0:
        return [0.0]
    if tolerance <= 20:
        return [-float(tolerance), 0.0, float(tolerance)]
    return [-float(tolerance), -float(tolerance) / 2.0, 0.0, float(tolerance) / 2.0, float(tolerance)]


def _gradient_image(gray: np.ndarray) -> np.ndarray:
    import cv2

    gray_f = gray.astype(np.float32)
    grad_x = cv2.Sobel(gray_f, cv2.CV_32F, 1, 0, ksize=3)
    grad_y = cv2.Sobel(gray_f, cv2.CV_32F, 0, 1, ksize=3)
    magnitude = cv2.magnitude(grad_x, grad_y)
    maximum = float(magnitude.max()) if magnitude.size else 0.0
    if maximum <= 1e-6:
        return np.zeros_like(gray, dtype=np.uint8)
    return np.clip(magnitude * (255.0 / maximum), 0, 255).astype(np.uint8)


def _transform_template(template: np.ndarray, scale: float, angle_deg: float) -> np.ndarray:
    import cv2

    width = max(4, int(round(template.shape[1] * scale)))
    height = max(4, int(round(template.shape[0] * scale)))
    interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_CUBIC
    transformed = cv2.resize(template, (width, height), interpolation=interpolation)
    if abs(angle_deg) < 1e-6:
        return transformed

    center = ((width - 1) / 2.0, (height - 1) / 2.0)
    matrix = cv2.getRotationMatrix2D(center, angle_deg, 1.0)
    cos_a = abs(float(matrix[0, 0]))
    sin_a = abs(float(matrix[0, 1]))
    rotated_width = max(4, int(math.ceil(height * sin_a + width * cos_a)))
    rotated_height = max(4, int(math.ceil(height * cos_a + width * sin_a)))
    matrix[0, 2] += (rotated_width - width) / 2.0
    matrix[1, 2] += (rotated_height - height) / 2.0
    return cv2.warpAffine(
        transformed,
        matrix,
        (rotated_width, rotated_height),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REFLECT_101,
    )


def _template_variants(
    template: np.ndarray,
    scale_tolerance: float,
    rotation_tolerance_deg: int,
) -> list[TemplateVariant]:
    variants: list[TemplateVariant] = []
    for scale in _scale_factors(scale_tolerance):
        for angle in _rotation_angles(rotation_tolerance_deg):
            gray = _transform_template(template, scale, angle)
            variants.append(TemplateVariant(scale, angle, gray, _gradient_image(gray)))
    return variants


def _local_peak_indices(response: np.ndarray, threshold: float, limit: int) -> np.ndarray:
    import cv2

    if response.size == 0:
        return np.empty(0, dtype=np.int64)
    finite = np.isfinite(response)
    dilated = cv2.dilate(np.where(finite, response, -1.0).astype(np.float32), np.ones((3, 3), np.uint8))
    mask = finite & (response >= threshold) & (response >= dilated - 1e-7)
    indices = np.flatnonzero(mask.ravel())
    if indices.size > limit:
        scores = response.ravel()[indices]
        top = np.argpartition(scores, -limit)[-limit:]
        indices = indices[top]
    return indices


def _safe_match(image: np.ndarray, template: np.ndarray) -> np.ndarray:
    import cv2

    if image.shape[0] < template.shape[0] or image.shape[1] < template.shape[1]:
        return np.empty((0, 0), dtype=np.float32)
    if float(template.std()) <= 1e-6:
        return np.zeros(
            (image.shape[0] - template.shape[0] + 1, image.shape[1] - template.shape[1] + 1),
            dtype=np.float32,
        )
    return cv2.matchTemplate(image, template, cv2.TM_CCOEFF_NORMED)


def _combined_response(
    gray: np.ndarray,
    edges: np.ndarray,
    variant: TemplateVariant,
    use_edges: bool,
) -> np.ndarray:
    intensity = _safe_match(gray, variant.gray)
    if not use_edges or intensity.size == 0 or float(variant.edges.std()) <= 1e-6:
        return intensity
    edge_response = _safe_match(edges, variant.edges)
    if edge_response.shape != intensity.shape:
        return intensity
    return (1.0 - EDGE_WEIGHT) * intensity + EDGE_WEIGHT * edge_response


def _appearance_compatible(candidate: np.ndarray, variant: TemplateVariant, use_edges: bool) -> bool:
    template_std = float(variant.gray.std())
    candidate_std = float(candidate.std())
    if template_std > 2.0:
        contrast_ratio = candidate_std / template_std
        if contrast_ratio < 0.40 or contrast_ratio > 2.50:
            return False
    if not use_edges:
        return True
    template_density = float(np.mean(variant.edges >= 32))
    if template_density < 0.01:
        return True
    candidate_density = float(np.mean(_gradient_image(candidate) >= 32))
    density_ratio = candidate_density / template_density
    return 0.35 <= density_ratio <= 2.80


def find_similar(
    project_id: str,
    scene_id: str,
    *,
    exemplar_bbox: list[float],
    class_id: int | None,
    threshold: float = DEFAULT_THRESHOLD,
    max_results: int = DEFAULT_MAX_RESULTS,
    nms_iou: float = 0.3,
    search_mode: Literal["local", "viewport", "scene"] = "local",
    search_bbox: list[float] | None = None,
    modality: str | None = None,
    scale_tolerance: float = DEFAULT_SCALE_TOLERANCE,
    rotation_tolerance_deg: int = DEFAULT_ROTATION_TOLERANCE_DEG,
    use_edges: bool = DEFAULT_USE_EDGES,
) -> list[dict[str, Any]]:
    """Return balanced scale/rotation-tolerant proposals for one exemplar."""
    import cv2

    from db.storage import load_scene_json
    from models.assistance import AssistanceProposal
    from services.scene_loader import get_scene_info
    from services.scene_raster_resolver import resolve_scene_raster

    scene = load_scene_json(project_id, scene_id, "scene", default={})
    if not scene:
        raise ExemplarError("Scene not found")

    ex = [float(v) for v in exemplar_bbox]
    tw, th = int(round(ex[2] - ex[0])), int(round(ex[3] - ex[1]))
    if tw < 4 or th < 4:
        raise ExemplarError("Exemplar is too small")

    scene_path = resolve_scene_raster(project_id, scene_id)
    info = get_scene_info(scene_path)
    scene_w, scene_h = int(info.width), int(info.height)

    region = resolve_search_region(search_mode, ex, scene_w, scene_h, search_bbox)
    if tw >= region.width or th >= region.height:
        raise ExemplarError("Exemplar is larger than the search region")

    exemplar_x = int(round(ex[0]))
    exemplar_y = int(round(ex[1]))
    exemplar_window = Window(exemplar_x, exemplar_y, tw, th)
    if exemplar_x < 0 or exemplar_y < 0 or exemplar_x + tw > scene_w or exemplar_y + th > scene_h:
        raise ExemplarError("Exemplar lies outside the scene")
    template_rgb = read_rgb_window(scene_path, exemplar_window, modality=modality)
    template = cv2.cvtColor(template_rgb, cv2.COLOR_RGB2GRAY)

    scales = _scale_factors(scale_tolerance)
    coarse_variants = [
        TemplateVariant(
            scale,
            angle,
            _transform_template(template, scale, angle),
            np.empty((0, 0), dtype=np.uint8),
        )
        for scale in scales
        for angle in _coarse_rotation_angles(rotation_tolerance_deg)
    ]
    variants = _template_variants(template, scale_tolerance, rotation_tolerance_deg)
    candidate_limit = max(max_results * 5, 800)
    per_block_limit = max(max_results, 240)
    coarse_threshold = max(0.20, float(threshold) - COARSE_THRESHOLD_MARGIN)
    candidate_heap: list[tuple[float, int, float, float, float, int, int]] = []
    sequence = 0
    max_coarse_w = max(variant.gray.shape[1] for variant in coarse_variants)
    max_coarse_h = max(variant.gray.shape[0] for variant in coarse_variants)
    for block in iter_search_blocks(region, max_coarse_w, max_coarse_h):
        rgb = read_rgb_window(scene_path, block, modality=modality)
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        for coarse in coarse_variants:
            coarse_h, coarse_w = coarse.gray.shape[:2]
            if coarse_w > block.width or coarse_h > block.height:
                continue
            response = _safe_match(gray, coarse.gray)
            hit_indices = _local_peak_indices(response, coarse_threshold, per_block_limit)
            if hit_indices.size == 0:
                continue
            flat = response.ravel()
            response_width = response.shape[1]
            for flat_index in hit_indices:
                score = float(flat[flat_index])
                ry, rx = divmod(int(flat_index), response_width)
                item = (
                    score,
                    sequence,
                    float(block.x0 + rx),
                    float(block.y0 + ry),
                    coarse.scale,
                    coarse_w,
                    coarse_h,
                )
                sequence += 1
                if len(candidate_heap) < candidate_limit:
                    heapq.heappush(candidate_heap, item)
                elif score > candidate_heap[0][0]:
                    heapq.heapreplace(candidate_heap, item)

    if not candidate_heap:
        return []
    ranked = sorted(candidate_heap, reverse=True)
    coarse_boxes = [[x, y, x + width, y + height] for _, _, x, y, _, width, height in ranked]
    coarse_scores = [score for score, *_ in ranked]
    coarse_keep = _nms(coarse_boxes, coarse_scores, 0.55)[:MAX_REFINEMENT_CANDIDATES]

    max_variant_w = max(variant.gray.shape[1] for variant in variants)
    max_variant_h = max(variant.gray.shape[0] for variant in variants)
    margin = max(4, int(round(max(tw, th) * REFINEMENT_MARGIN_FRACTION)))
    refined: list[tuple[float, list[float], float, float]] = []
    for coarse_index in coarse_keep:
        coarse_box = coarse_boxes[coarse_index]
        center_x = (coarse_box[0] + coarse_box[2]) / 2.0
        center_y = (coarse_box[1] + coarse_box[3]) / 2.0
        roi_x0 = max(region.x0, int(math.floor(center_x - max_variant_w / 2.0 - margin)))
        roi_y0 = max(region.y0, int(math.floor(center_y - max_variant_h / 2.0 - margin)))
        roi_x1 = min(region.x0 + region.width, int(math.ceil(center_x + max_variant_w / 2.0 + margin)))
        roi_y1 = min(region.y0 + region.height, int(math.ceil(center_y + max_variant_h / 2.0 + margin)))
        if roi_x1 - roi_x0 < 4 or roi_y1 - roi_y0 < 4:
            continue
        roi_window = Window(roi_x0, roi_y0, roi_x1 - roi_x0, roi_y1 - roi_y0)
        roi_rgb = read_rgb_window(scene_path, roi_window, modality=modality)
        roi_gray = cv2.cvtColor(roi_rgb, cv2.COLOR_RGB2GRAY)
        roi_edges = _gradient_image(roi_gray) if use_edges else np.empty((0, 0), dtype=np.uint8)
        best: tuple[float, list[float], float, float] | None = None
        for variant in variants:
            response = _combined_response(roi_gray, roi_edges, variant, use_edges)
            if response.size == 0:
                continue
            variant_h, variant_w = variant.gray.shape[:2]
            peak_indices = _local_peak_indices(response, float(threshold), 5)
            if peak_indices.size == 0:
                continue
            flat = response.ravel()
            for flat_index in peak_indices[np.argsort(-flat[peak_indices])]:
                location_y, location_x = divmod(int(flat_index), response.shape[1])
                candidate_patch = roi_gray[
                    location_y:location_y + variant_h,
                    location_x:location_x + variant_w,
                ]
                if not _appearance_compatible(candidate_patch, variant, use_edges):
                    continue
                box = [
                    float(roi_x0 + location_x),
                    float(roi_y0 + location_y),
                    float(roi_x0 + location_x + variant_w),
                    float(roi_y0 + location_y + variant_h),
                ]
                candidate = (float(flat[flat_index]), box, variant.scale, variant.angle_deg)
                if best is None or candidate[0] > best[0]:
                    best = candidate
        if best is not None and best[0] >= threshold:
            refined.append(best)

    if not refined:
        return []
    refined.sort(key=lambda item: item[0], reverse=True)
    boxes = [item[1] for item in refined]
    box_scores = [item[0] for item in refined]
    keep = _nms(boxes, box_scores, nms_iou)

    proposals: list[dict[str, Any]] = []
    for idx in keep:
        box = boxes[idx]
        if _iou(box, ex) >= SELF_IOU:
            continue  # the exemplar itself
        payload = AssistanceProposal(
            session_id="",
            source_tool="exemplar",
            geometry_type="bbox",
            bbox=box,
            class_id=class_id,
            confidence=round(box_scores[idx], 4),
            model_name="template_ncc_v2",
            working_grid_uid=scene.get("working_grid_uid"),
            source_window=[region.x0, region.y0, region.width, region.height],
        ).model_dump()
        payload["_match_scale"] = refined[idx][2]
        payload["_match_angle_deg"] = refined[idx][3]
        proposals.append(payload)
        if len(proposals) >= max_results:
            break
    return proposals
