"""Kolor klasy: wybor uzytkownika ma sie zapisac, literowka ma zostac odrzucona.

Kolor da sie teraz ustawic w UI (kwadrat z probka przy klasie), wiec przechodzi przez te
dwa endpointy juz nie tylko z palety domyslnej. Dwie rzeczy musza byc pewne: jawnie
wskazany kolor nie zostaje podmieniony, a tekst, ktory nie jest kolorem, nie trafia do
pliku klas - niewidoczna ramka na scenie jest trudniejsza do zdiagnozowania niz blad przy
zapisie.
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from models.annotation import ClassCreate, ClassUpdate
from routers.classes import DEFAULT_COLORS, create_class, update_class


@pytest.fixture(autouse=True)
def _isolated_data_dir(monkeypatch, tmp_path):
    from db import storage

    monkeypatch.setattr(storage, "DATA_DIR", tmp_path / "appdata")


@pytest.fixture
def project_id() -> str:
    from db.storage import save_json

    pid = "proj-class-color"
    save_json(pid, "project", {"id": pid, "name": "class colours"})
    return pid


def _create(project_id: str, name: str, **kwargs) -> dict:
    return asyncio.run(create_class(project_id, ClassCreate(name=name, **kwargs)))


def test_a_colour_the_user_picked_is_kept(project_id):
    """Regresja: `#FF0000` bylo traktowane jak „nie podano" i podmieniane na kolor z palety."""
    _create(project_id, "first")  # zajmuje id 0
    second = _create(project_id, "second", color="#FF0000")
    assert second["id"] == 1
    assert DEFAULT_COLORS[1] != "#FF0000", "test bylby pusty, gdyby paleta dawala tu czerwony"
    assert second["color"] == "#FF0000"


def test_no_colour_still_falls_back_to_the_default_palette(project_id):
    assert _create(project_id, "first")["color"] == DEFAULT_COLORS[0]
    assert _create(project_id, "second")["color"] == DEFAULT_COLORS[1]


def test_update_writes_the_new_colour_to_the_class_file(project_id):
    from db.storage import load_json

    created = _create(project_id, "vehicle")
    updated = asyncio.run(update_class(project_id, created["id"], ClassUpdate(color="#1A9E5C")))
    assert updated["color"] == "#1A9E5C"
    stored = load_json(project_id, "classes", default=[])
    assert [c["color"] for c in stored] == ["#1A9E5C"]


def test_update_without_a_colour_leaves_it_alone(project_id):
    created = _create(project_id, "vehicle", color="#1A9E5C")
    updated = asyncio.run(update_class(project_id, created["id"], ClassUpdate(name="ship")))
    assert updated["name"] == "ship"
    assert updated["color"] == "#1A9E5C"


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("#1a9e5c", "#1A9E5C"),
        ("1A9E5C", None),  # bez `#` to nie jest zapis, ktory wysyla UI
        ("#0f8", "#00FF88"),
        ("  #1A9E5C  ", "#1A9E5C"),
    ],
)
def test_hex_is_normalised(given, expected):
    if expected is None:
        with pytest.raises(ValidationError):
            ClassUpdate(color=given)
    else:
        assert ClassUpdate(color=given).color == expected


@pytest.mark.parametrize("given", ["red", "#12345", "#GGGGGG", "", "rgb(1,2,3)"])
def test_anything_that_is_not_a_hex_colour_is_rejected(given):
    with pytest.raises(ValidationError):
        ClassCreate(name="vehicle", color=given)
    with pytest.raises(ValidationError):
        ClassUpdate(color=given)


def test_omitting_the_colour_is_not_the_same_as_sending_one():
    assert ClassCreate(name="vehicle").color is None
    assert ClassUpdate().color is None
