"""Bramka DINO: brak wag to inny powód niż brak kodu backbone'u.

Od wydania, które nie niesie kodu repo DINOv3 (licencja nie jest otwarta), oba przypadki
spotykają zwykłego użytkownika i wymagają od niego różnych czynności. Wcześniej jeden
komunikat mówił „brak wag" także wtedy, gdy wagi leżały na dysku, a auto-wybór egzemplarza
sięgał po DINOv3-SAT, czego nie dało się wczytać.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from services import embedding_backbone as eb
from services.dino_exemplar_assist import _default_exemplar_checkpoint

V2_WEIGHTS = "dinov2_vits14_reg4_pretrain.pth"
V3_WEIGHTS = "dinov3_vitl16_pretrain_sat493m.pth"


@pytest.fixture
def dino_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Pusty MODELS_ROOT/dino bez kodu repo: stan świeżej instalacji wydania."""
    models = tmp_path / "models" / "dino"
    models.mkdir(parents=True)
    monkeypatch.setattr(eb, "DINO_DIR", models)
    monkeypatch.setattr(eb, "_BUNDLED_DINO_DIR", tmp_path / "vendor" / "dino")
    # Trzecia ścieżka poszukiwań to cache torch.hub - na maszynie deweloperskiej może
    # istnieć i zafałszowałby wynik, więc wskazujemy pusty katalog.
    monkeypatch.setenv("TORCH_HOME", str(tmp_path / "torch-home"))
    monkeypatch.delenv("GEOTILE_DINO_ALLOW_DOWNLOAD", raising=False)
    # Testujemy bramkę wag i kodu repo, nie instalację torcha: runtime pakowany
    # z aplikacją ma torcha, ale nie ma pytesta, więc obecność deklarujemy.
    monkeypatch.setattr(eb, "_torch_installed", lambda: True)
    return models


def _weights(dino_dir: Path, *names: str) -> None:
    for name in names:
        (dino_dir / name).write_bytes(b"")


def test_missing_weights_is_reported_as_missing_weights(dino_dir: Path) -> None:
    (dino_dir / "dinov2_repo").mkdir()

    reason = eb.dino_unavailable_reason()

    assert reason is not None
    assert "brak wag" in reason


def test_dinov2_works_on_the_code_that_ships_with_the_application(dino_dir: Path) -> None:
    (dino_dir / "dinov2_repo").mkdir()
    _weights(dino_dir, V2_WEIGHTS)

    assert eb.dino_unavailable_reason() is None
    assert eb.dino_runtime_available() is True


def test_dinov3_weights_without_repo_code_name_the_repo_not_the_weights(dino_dir: Path) -> None:
    """Najczęstszy przypadek po usunięciu kodu DINOv3 z instalatora."""
    _weights(dino_dir, V3_WEIGHTS)

    reason = eb.dino_unavailable_reason()

    assert reason is not None
    assert "dinov3_repo" in reason
    assert "brak wag" not in reason


def test_download_flag_does_not_unlock_dinov3(dino_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """DINOv3 nie ma ścieżki przez torch.hub (hubconf ciągnie torchmetrics), więc flaga
    pobierania nie może przepuścić bramki - inaczej zieleni się coś, co padnie przy użyciu."""
    _weights(dino_dir, V3_WEIGHTS)
    monkeypatch.setenv("GEOTILE_DINO_ALLOW_DOWNLOAD", "1")

    assert eb.dino_runtime_available() is False


def test_download_flag_still_unlocks_dinov2(dino_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _weights(dino_dir, V2_WEIGHTS)
    monkeypatch.setenv("GEOTILE_DINO_ALLOW_DOWNLOAD", "1")

    assert eb.dino_runtime_available() is True


def test_mixed_weights_fall_back_to_dinov2(dino_dir: Path) -> None:
    """Degradacja jest łagodna: projekt z wagami obu rodzin liczy się na DINOv2."""
    (dino_dir / "dinov2_repo").mkdir()
    _weights(dino_dir, V2_WEIGHTS, V3_WEIGHTS)

    assert eb.dino_unavailable_reason() is None
    assert eb.resolve_dino_checkpoint().name == V2_WEIGHTS


def test_exemplar_default_skips_dinov3_sat_without_repo_code(dino_dir: Path) -> None:
    """Auto-wybór nie może wskazać checkpointu, którego nie da się wczytać."""
    (dino_dir / "dinov2_repo").mkdir()
    _weights(dino_dir, V2_WEIGHTS, V3_WEIGHTS)

    assert _default_exemplar_checkpoint() is None


def test_missing_torch_is_reported_before_anything_else(dino_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (dino_dir / "dinov2_repo").mkdir()
    _weights(dino_dir, V2_WEIGHTS)
    monkeypatch.setattr(eb, "_torch_installed", lambda: False)

    reason = eb.dino_unavailable_reason()

    assert reason is not None
    assert "torch" in reason


def test_user_supplied_repo_code_restores_dinov3(dino_dir: Path) -> None:
    """To, co użytkownik robi po przeczytaniu komunikatu: klonuje repo do MODELS_ROOT/dino."""
    _weights(dino_dir, V3_WEIGHTS)
    (dino_dir / "dinov3_repo").mkdir()

    assert eb.dino_unavailable_reason() is None
    assert _default_exemplar_checkpoint() == V3_WEIGHTS
