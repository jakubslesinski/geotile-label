import math
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator
from datetime import datetime, timezone
import uuid


def _annotation_id() -> str:
    return uuid.uuid4().hex[:10]


GeometryType = Literal["bbox", "rotated_bbox", "polygon", "mask"]


class RotatedBBox(BaseModel):
    cx: float
    cy: float
    width: float
    height: float
    angle_deg: float


def _bbox_from_points(points: list[list[float]]) -> list[float] | None:
    if not points:
        return None
    xs = [float(point[0]) for point in points if len(point) >= 2]
    ys = [float(point[1]) for point in points if len(point) >= 2]
    if not xs or not ys:
        return None
    return [min(xs), min(ys), max(xs), max(ys)]


def _rotated_bbox_polygon(rotated_bbox: RotatedBBox) -> list[list[float]]:
    half_width = rotated_bbox.width / 2
    half_height = rotated_bbox.height / 2
    angle = math.radians(rotated_bbox.angle_deg)
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)
    corners = [
        (-half_width, -half_height),
        (half_width, -half_height),
        (half_width, half_height),
        (-half_width, half_height),
    ]
    return [
        [
            rotated_bbox.cx + dx * cos_a - dy * sin_a,
            rotated_bbox.cy + dx * sin_a + dy * cos_a,
        ]
        for dx, dy in corners
    ]


def _normalize_bbox_fields(
    geometry_type: str,
    bbox: list[float] | None,
    rotated_bbox: RotatedBBox | None,
    polygon_scene_px: list[list[float]] | None,
    front_vector_scene_px: list[float] | None,
    orientation_angle_deg: float | None,
) -> tuple[list[float] | None, list[list[float]] | None, list[float] | None, float | None]:
    polygon = polygon_scene_px
    vector = front_vector_scene_px
    orientation = orientation_angle_deg

    if geometry_type == "rotated_bbox" and rotated_bbox:
        if polygon is None:
            polygon = _rotated_bbox_polygon(rotated_bbox)
        if orientation is None:
            orientation = rotated_bbox.angle_deg
        if vector is None:
            angle = math.radians(rotated_bbox.angle_deg)
            vector = [math.cos(angle), math.sin(angle)]

    normalized_bbox = bbox
    if normalized_bbox is None and polygon:
        normalized_bbox = _bbox_from_points(polygon)

    return normalized_bbox, polygon, vector, orientation


class AnnotationCreate(BaseModel):
    class_id: int
    geometry_type: GeometryType = "bbox"
    bbox: list[float] | None = None  # [x_min, y_min, x_max, y_max] scene pixels
    rotated_bbox: RotatedBBox | None = None
    polygon_scene_px: list[list[float]] | None = None
    front_edge_scene_px: list[list[float]] | None = None
    front_vector_scene_px: list[float] | None = None
    orientation_angle_deg: float | None = None
    is_negative: bool = False

    @model_validator(mode="after")
    def validate_geometry(self):
        bbox, polygon, vector, orientation = _normalize_bbox_fields(
            self.geometry_type,
            self.bbox,
            self.rotated_bbox,
            self.polygon_scene_px,
            self.front_vector_scene_px,
            self.orientation_angle_deg,
        )
        if bbox is None:
            raise ValueError("bbox is required or must be derivable from rotated_bbox/polygon_scene_px")
        self.bbox = bbox
        self.polygon_scene_px = polygon
        self.front_vector_scene_px = vector
        self.orientation_angle_deg = orientation
        return self


class AnnotationUpdate(BaseModel):
    class_id: int | None = None
    geometry_type: GeometryType | None = None
    bbox: list[float] | None = None
    rotated_bbox: RotatedBBox | None = None
    polygon_scene_px: list[list[float]] | None = None
    front_edge_scene_px: list[list[float]] | None = None
    front_vector_scene_px: list[float] | None = None
    orientation_angle_deg: float | None = None
    is_negative: bool | None = None

    @model_validator(mode="after")
    def derive_bbox_if_possible(self):
        """Uzupelnij pola wyprowadzalne, ale NIGDY nie zmyslaj ksztaltu.

        Aktualizacja rozni sie tu od tworzenia. Przy tworzeniu wolno odtworzyc poligon
        z `rotated_bbox`, bo nie ma czego nadpisac. Przy aktualizacji zapisany
        `polygon_scene_px` moze niesc SCINANIE — na scenach, gdzie piksel↔mapa nie jest
        konforemne, ramka wierna mapie jest w pikselach rownoleglobokiem. `rotated_bbox`
        opisuje wylacznie prostokat, wiec wygenerowanie z niego poligonu podmienialoby
        rzeczywisty ksztalt na prostokat pikselowy, ktory na mapie wyglada na przekoszony.
        Klient, ktory chce zmienic ksztalt, ma przyslac `polygon_scene_px` jawnie.
        """
        geometry_fields = {
            "geometry_type", "bbox", "rotated_bbox", "polygon_scene_px",
            "front_vector_scene_px", "orientation_angle_deg",
        }
        if not geometry_fields.intersection(self.model_fields_set):
            return self

        # Zmiana `rotated_bbox` bez poligonu zostawilaby zapisany `polygon_scene_px`
        # w starym ksztalcie, a to on jest zrodlem prawdy dla rysowania i edycji — wynik
        # bylby niespojna para. Nie zgadujemy, ktore z dwoch pol jest aktualne: taka
        # aktualizacja jest odrzucana.
        if "rotated_bbox" in self.model_fields_set and self.rotated_bbox is not None:
            if "polygon_scene_px" not in self.model_fields_set:
                raise ValueError(
                    "rotated_bbox update must include polygon_scene_px "
                    "(the polygon is the source of truth for shape)"
                )

        polygon = self.polygon_scene_px
        vector = self.front_vector_scene_px
        orientation = self.orientation_angle_deg

        if self.rotated_bbox:
            if orientation is None:
                orientation = self.rotated_bbox.angle_deg
            if vector is None:
                angle = math.radians(self.rotated_bbox.angle_deg)
                vector = [math.cos(angle), math.sin(angle)]

        bbox = self.bbox
        if bbox is None:
            if polygon:
                bbox = _bbox_from_points(polygon)
            elif self.rotated_bbox:
                # Sam `bbox` opisujacy wolno policzyc — to metadana wyszukiwania, a nie
                # ksztalt. Poligon zostaje nietkniety.
                bbox = _bbox_from_points(_rotated_bbox_polygon(self.rotated_bbox))

        self.bbox = bbox
        self.polygon_scene_px = polygon
        self.front_vector_scene_px = vector
        self.orientation_angle_deg = orientation
        return self


class Annotation(BaseModel):
    id: str = Field(default_factory=_annotation_id)
    source_annotation_id: str | None = None
    scene_id: str | None = None
    class_id: int
    geometry_type: GeometryType = "bbox"
    bbox: list[float]
    rotated_bbox: RotatedBBox | None = None
    polygon_scene_px: list[list[float]] | None = None
    front_edge_scene_px: list[list[float]] | None = None
    front_vector_scene_px: list[float] | None = None
    orientation_angle_deg: float | None = None
    is_negative: bool = False
    annotation_source: str = "manual"
    annotator_email: str | None = None
    created_by: str | None = None
    updated_by: str | None = None
    source_model: str | None = None
    import_id: str | None = None
    source_package_id: str | None = None
    copied_from_scene_id: str | None = None
    copied_from_annotation_id: str | None = None
    attributes: dict[str, Any] | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


_HEX_COLOR = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def _hex_color(value: str | None) -> str | None:
    """Normalizuje kolor klasy do `#RRGGBB` wielkimi literami.

    Kolor jest wpisywany recznie w UI, wiec literowka ma zostac odrzucona tutaj, a nie
    zapisana do pliku klas i objawic sie dopiero jako niewidoczna ramka na scenie.
    """
    if value is None:
        return None
    text = value.strip()
    if not _HEX_COLOR.match(text):
        raise ValueError(f"color must be a hex value like #RRGGBB, got {value!r}")
    if len(text) == 4:
        text = "#" + "".join(char * 2 for char in text[1:])
    return text.upper()


class ClassCreate(BaseModel):
    name: str
    # `None`, a nie `#FF0000`: inaczej nie da sie odroznic „nie podano koloru" od
    # „wybrano czerwony", a `create_class` podmienia pierwsze na kolor z palety.
    color: str | None = None
    hotkey: int | None = None  # 1-9

    _normalize_color = field_validator("color")(_hex_color)


class ClassUpdate(BaseModel):
    name: str | None = None
    color: str | None = None
    hotkey: int | None = None

    _normalize_color = field_validator("color")(_hex_color)


class LabelClass(BaseModel):
    id: int
    name: str
    color: str = "#FF0000"
    hotkey: int | None = None
