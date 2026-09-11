"""Walidacja pliku klas przy imporcie.

Do tej pory import sprawdzal WYLACZNIE to, ze najwyzszy poziom jest tablica, i zapisywal
zawartosc doslownie. Plik o innym ksztalcie elementow przechodzil bez slowa, a objaw
pojawial sie dwa ekrany dalej: lista z poprawnym licznikiem i pustymi wierszami. Te testy
pilnuja, zeby blad byl zglaszany tam, gdzie powstaje.

Ksztalty pochodza z realnych narzedzi: lista napisow (proste eksporty), `class_name`/`rgb`
(konwertery), `categories` z COCO.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from routers.classes import DEFAULT_COLORS, _normalize_imported_classes


VALID = [
    {"id": 0, "name": "samolot_transportowy_kategoria_i", "color": "#2EC3FA", "hotkey": 1},
    {"id": 1, "name": "pojazd_transportowy", "color": "#34C0F9", "hotkey": None},
]


def test_valid_file_passes_through_unchanged():
    result = _normalize_imported_classes(VALID)
    assert result == [
        {"id": 0, "name": "samolot_transportowy_kategoria_i", "color": "#2EC3FA", "hotkey": 1},
        {"id": 1, "name": "pojazd_transportowy", "color": "#34C0F9", "hotkey": None},
    ]


def test_list_of_strings_is_rejected_with_a_usable_message():
    with pytest.raises(HTTPException) as excinfo:
        _normalize_imported_classes(["car", "ship"])
    assert excinfo.value.status_code == 400
    assert "Class #0" in excinfo.value.detail
    assert "str" in excinfo.value.detail


def test_foreign_key_names_are_rejected_and_the_message_lists_what_was_found():
    """Uzytkownik ma sie dowiedziec, czego brakuje i co bylo w pliku."""
    with pytest.raises(HTTPException) as excinfo:
        _normalize_imported_classes([{"class_id": 0, "class_name": "car", "rgb": [255, 0, 0]}])
    detail = excinfo.value.detail
    assert "missing required field(s): id, name" in detail
    assert "class_id" in detail and "class_name" in detail


def test_coco_categories_are_accepted_and_get_a_default_colour():
    """Brak koloru to brak prezentacji, nie brak danych — uzupelniamy jak `create_class`."""
    result = _normalize_imported_classes(
        [{"id": 1, "name": "car", "supercategory": "vehicle"}]
    )
    assert result[0]["name"] == "car"
    assert result[0]["color"] == DEFAULT_COLORS[1 % len(DEFAULT_COLORS)]
    assert result[0]["hotkey"] is None


def test_nested_lists_are_rejected():
    with pytest.raises(HTTPException):
        _normalize_imported_classes([[0, "car", "#FF0000"]])


def test_empty_name_is_rejected():
    with pytest.raises(HTTPException) as excinfo:
        _normalize_imported_classes([{"id": 0, "name": ""}])
    assert "name" in excinfo.value.detail


def test_duplicate_ids_are_rejected():
    """Powielone id rozjezdzaja przypisanie adnotacji i daja duplikaty kluczy w UI."""
    with pytest.raises(HTTPException) as excinfo:
        _normalize_imported_classes(
            [{"id": 3, "name": "a"}, {"id": 3, "name": "b"}]
        )
    assert "Duplicate class id 3" in excinfo.value.detail


def test_non_numeric_id_is_rejected():
    with pytest.raises(HTTPException) as excinfo:
        _normalize_imported_classes([{"id": "abc", "name": "car"}])
    assert "non-numeric" in excinfo.value.detail


def test_malformed_colour_falls_back_to_the_palette():
    result = _normalize_imported_classes(
        [{"id": 0, "name": "car", "color": "czerwony"},
         {"id": 1, "name": "ship", "color": "#GGGGGG"},
         {"id": 2, "name": "plane", "color": "#abc"}]
    )
    assert [item["color"] for item in result] == [
        DEFAULT_COLORS[0], DEFAULT_COLORS[1], DEFAULT_COLORS[2]
    ]


def test_lowercase_hex_is_preserved():
    result = _normalize_imported_classes([{"id": 0, "name": "car", "color": "#2ec3fa"}])
    assert result[0]["color"] == "#2ec3fa"


def test_string_hotkey_is_coerced_and_garbage_becomes_none():
    result = _normalize_imported_classes(
        [{"id": 0, "name": "a", "hotkey": "3"}, {"id": 1, "name": "b", "hotkey": "x"}]
    )
    assert result[0]["hotkey"] == 3
    assert result[1]["hotkey"] is None


def test_empty_file_is_allowed():
    """Pusta lista jest poprawnym wynikiem — kasuje klasy projektu."""
    assert _normalize_imported_classes([]) == []


def test_real_sar_class_file_shape_is_accepted():
    """Ksztalt zgloszonego pliku: 53 klasy, hotkey tylko dla pierwszych dziewieciu."""
    raw = [
        {"id": index, "name": f"klasa_{index}", "color": "#2EC3FA",
         "hotkey": index + 1 if index < 9 else None}
        for index in range(53)
    ]
    result = _normalize_imported_classes(raw)
    assert len(result) == 53
    assert all(item["name"] and item["color"] for item in result)
    assert sum(1 for item in result if item["hotkey"] is not None) == 9
