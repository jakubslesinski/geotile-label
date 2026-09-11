"""Aktualizacja geometrii adnotacji nie moze zmyslac ksztaltu.

Regresja do bledu zgloszonego 2026-09-04: ramka zorientowana narysowana poprawnie
(prostokatna na mapie) po edycji bywala przekoszona — na scenie EPSG:4326 wygladala jak
rownoleglobok. Jedna z drog, ktora ten stan mogl powstac, byla po stronie API: przy
aktualizacji `AnnotationUpdate` odtwarzalo `polygon_scene_px` z `rotated_bbox`, czyli
podmienialo rzeczywisty ksztalt na PROSTOKAT W PIKSELACH.

Na scenach, gdzie piksel↔mapa nie jest konforemne, prostokat w pikselach jest na mapie
rownolegobokiem — dokladnie objaw ze zgloszenia. Poligon jest zrodlem prawdy dla ksztaltu
i API nie ma prawa go wygenerowac za klienta.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from models.annotation import AnnotationCreate, AnnotationUpdate

#: Ramka wierna mapie na scenie niekonforemnej: w pikselach to rownoleglobok, wiec zaden
#: `rotated_bbox` nie opisze jej bez straty.
SHEARED = [[0.0, 0.0], [20.0, 0.0], [18.0, 10.0], [2.0, 10.0]]
ROTATED = {"cx": 10.0, "cy": 5.0, "width": 20.0, "height": 10.0, "angle_deg": 0.0}


def test_update_keeps_client_polygon_untouched():
    update = AnnotationUpdate(
        geometry_type="rotated_bbox",
        rotated_bbox=ROTATED,
        polygon_scene_px=SHEARED,
    )
    assert update.polygon_scene_px == SHEARED


def test_update_does_not_invent_polygon_from_rotated_bbox():
    """Sedno regresji: brak poligonu w payloadzie NIE moze wyprodukowac prostokata.

    Wczesniej `polygon_scene_px` wychodzilo z walidatora jako cztery rogi prostokata
    policzone z `rotated_bbox`, a router zapisywal je na zapisanym, scinanym poligonie.
    """
    with pytest.raises(ValidationError) as excinfo:
        AnnotationUpdate(geometry_type="rotated_bbox", rotated_bbox=ROTATED)
    assert "polygon_scene_px" in str(excinfo.value)


def test_update_without_geometry_type_is_still_guarded():
    """Payload bez `geometry_type` szedl wczesniej sciezka `bbox` i omijal cala galaz."""
    with pytest.raises(ValidationError):
        AnnotationUpdate(rotated_bbox=ROTATED)


def test_update_may_clear_polygon_explicitly():
    """Jawne `null` jest decyzja klienta, nie zgadywaniem — musi przejsc."""
    update = AnnotationUpdate(
        geometry_type="rotated_bbox",
        rotated_bbox=ROTATED,
        polygon_scene_px=None,
    )
    assert update.polygon_scene_px is None
    assert "polygon_scene_px" in update.model_fields_set


def test_update_derives_only_the_enclosing_bbox():
    """`bbox` to metadana wyszukiwania i wolno ja policzyc; ksztalt — nie."""
    update = AnnotationUpdate(geometry_type="rotated_bbox", polygon_scene_px=SHEARED)
    assert update.bbox == [0.0, 0.0, 20.0, 10.0]
    assert update.polygon_scene_px == SHEARED


def test_update_without_geometry_fields_changes_nothing():
    update = AnnotationUpdate(class_id=7)
    assert update.bbox is None
    assert update.polygon_scene_px is None


def test_create_may_still_derive_polygon_from_rotated_bbox():
    """Tworzenie zostaje bez zmian: nie ma zapisanego ksztaltu, ktory dalo by sie nadpisac."""
    created = AnnotationCreate(class_id=1, geometry_type="rotated_bbox", rotated_bbox=ROTATED)
    assert created.polygon_scene_px is not None
    assert len(created.polygon_scene_px) == 4
    assert created.bbox == [0.0, 0.0, 20.0, 10.0]
