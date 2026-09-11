"""Dwuetapowa publikacja derywatu 1x i odzyskiwanie po przerwaniu (R1.4).

Dlaczego to jest osobna maszyna stanow
-------------------------------------
Aktywacja dotyka TRZECH miejsc po kolei: pliku derywatu, manifestu sceny i `scene.json`.
To nie jest jedna transakcja i nie da sie jej nia zrobic bez przepisania warstwy zapisu.
Zamknięcie aplikacji miedzy tymi krokami zostawia stan czesciowy: plik jest, manifest
mowi o nim, a `scene.json` jeszcze nie — albo dowolna inna kombinacja.

Zamiast udawac atomowosc, ZAPISUJEMY POSTEP. Rekord stanu mowi, ktore kroki juz przeszly,
wiec przy starcie da sie stwierdzic, czy dokonczyc aktywacje, czy ja wycofac. Bez tego
jedyna bezpieczna reakcja byloby porzucenie dwudziestominutowej pracy przy kazdym
niefortunnym zamknieciu.

Stany
-----
`building` -> `validating` -> `candidate_ready` -> `active`

`candidate_ready` jest tu istotny: plik jest gotowy i sprawdzony, ale jeszcze nie sluzy
do wyswietlania. Dopiero z tego stanu wolno przelaczac — i tylko po ponownym sprawdzeniu,
czy zrodlo sie nie zmienilo (R1.2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from services.jobs.store import read_json, write_json_atomic

STATE_BUILDING = "building"
STATE_VALIDATING = "validating"
STATE_CANDIDATE_READY = "candidate_ready"
STATE_ACTIVE = "active"
STATE_FAILED = "failed"

#: Kroki aktywacji, w kolejnosci. Kolejnosc jest czescia kontraktu: manifest wskazuje
#: plik, a `scene.json` wskazuje manifest, wiec odwrotna kolejnosc tworzylaby wpis
#: wskazujacy na cos, czego jeszcze nie ma.
STEP_ARTIFACT = "artifact_published"
STEP_MANIFEST = "manifest_updated"
STEP_SCENE = "scene_updated"
ACTIVATION_STEPS = (STEP_ARTIFACT, STEP_MANIFEST, STEP_SCENE)

SCHEMA_NAME = "geotile_fullres_publication"
SCHEMA_VERSION = 1


@dataclass
class PublicationRecord:
    """Trwaly zapis postepu — jedyne zrodlo prawdy przy odzyskiwaniu."""

    state: str = STATE_BUILDING
    steps_done: list[str] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_name": SCHEMA_NAME,
            "schema_version": SCHEMA_VERSION,
            "state": self.state,
            "steps_done": list(self.steps_done),
            "payload": dict(self.payload),
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "PublicationRecord":
        data = data or {}
        return cls(
            state=str(data.get("state") or STATE_BUILDING),
            steps_done=[str(item) for item in (data.get("steps_done") or [])],
            payload=dict(data.get("payload") or {}),
            error=data.get("error"),
        )


def read_publication_record(path: Path) -> PublicationRecord | None:
    """Read a durable publication record, rejecting unknown/corrupt schemas.

    The active COG resolver deliberately fails closed: a TIFF without this record is
    only an old or interrupted candidate, never an asset that may be rendered.
    """

    data = read_json(path, default={}) or {}
    if (
        data.get("schema_name") != SCHEMA_NAME
        or int(data.get("schema_version") or 0) != SCHEMA_VERSION
    ):
        return None
    return PublicationRecord.from_dict(data)


def write_publication_record(path: Path, record: PublicationRecord) -> None:
    """Atomically persist one publication transition."""

    write_json_atomic(path, record.as_dict())


def mark_failed(
    record: PublicationRecord,
    *,
    error: str,
    error_code: str | None = None,
) -> PublicationRecord:
    """Move an in-progress publication to a durable failed state."""

    if record.state != STATE_FAILED:
        advance(record, STATE_FAILED)
    record.error = error
    if error_code:
        record.payload["error_code"] = error_code
    return record


_ALLOWED_TRANSITIONS = {
    STATE_BUILDING: {STATE_VALIDATING, STATE_FAILED},
    STATE_VALIDATING: {STATE_CANDIDATE_READY, STATE_FAILED},
    STATE_CANDIDATE_READY: {STATE_ACTIVE, STATE_FAILED},
    STATE_ACTIVE: set(),
    STATE_FAILED: {STATE_BUILDING},
}


class InvalidTransitionError(RuntimeError):
    pass


def advance(record: PublicationRecord, target: str) -> PublicationRecord:
    """Przejscie do kolejnego stanu, z odrzuceniem skrotow.

    Skrot z `building` prosto do `active` oznaczalby publikacje bez walidacji — bramka
    z §19 wymaga sprawdzenia KAZDEGO derywatu przed aktywacja, wiec ta droga nie moze
    istniec nawet przez pomylke.
    """
    allowed = _ALLOWED_TRANSITIONS.get(record.state, set())
    if target not in allowed:
        raise InvalidTransitionError(f"{record.state} -> {target}")
    record.state = target
    if target == STATE_BUILDING:
        record.steps_done = []
        record.error = None
    return record


def mark_step(record: PublicationRecord, step: str) -> PublicationRecord:
    if step not in ACTIVATION_STEPS:
        raise ValueError(f"nieznany krok aktywacji: {step}")
    if step not in record.steps_done:
        record.steps_done.append(step)
    return record


def remaining_steps(record: PublicationRecord) -> list[str]:
    return [step for step in ACTIVATION_STEPS if step not in record.steps_done]


# --- odzyskiwanie ---------------------------------------------------------------------

RECOVERY_NOTHING = "nothing_to_do"
RECOVERY_RESUME = "resume_activation"
RECOVERY_ROLLBACK = "rollback"
RECOVERY_RESTART = "restart_build"


@dataclass(frozen=True)
class RecoveryPlan:
    action: str
    reason: str
    steps: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"action": self.action, "reason": self.reason, "steps": list(self.steps)}


def plan_recovery(
    record: PublicationRecord,
    *,
    artifact_present: bool,
    source_still_matches: bool,
) -> RecoveryPlan:
    """Co zrobic ze stanem zastanym przy starcie aplikacji.

    Trzy rzeczy decyduja: w jakim stanie zapisano postep, czy plik derywatu istnieje
    i czy zrodlo sie nie zmienilo. Ostatnie jest konieczne, bo aplikacja mogla byc
    zamknieta przez tydzien, a scena w tym czasie zrelinkowana.
    """
    if record.state == STATE_ACTIVE and not remaining_steps(record):
        return RecoveryPlan(RECOVERY_NOTHING, "already_active")

    if not artifact_present:
        # Bez pliku nie ma czego aktywowac ani wycofywac — pozostale slady sprzatamy
        # i scena wraca do stanu sprzed proby.
        return RecoveryPlan(RECOVERY_RESTART, "artifact_missing")

    if not source_still_matches:
        # Plik opisuje piksele, ktorych juz nie ma. Aktywacja bylaby publikacja
        # nieaktualnego obrazu — gorsza niz brak derywatu.
        return RecoveryPlan(RECOVERY_ROLLBACK, "source_changed_since_build")

    if record.state in (STATE_BUILDING, STATE_VALIDATING):
        # Przerwanie przed potwierdzeniem poprawnosci. Plik moze byc niepelny, a nie
        # umiemy tego stwierdzic inaczej niz ponowna walidacja — taniej zbudowac na nowo
        # niz ryzykowac publikacje uciętego rastra.
        return RecoveryPlan(RECOVERY_RESTART, f"interrupted_in_{record.state}")

    if record.state == STATE_CANDIDATE_READY or remaining_steps(record):
        return RecoveryPlan(
            RECOVERY_RESUME, "activation_incomplete", remaining_steps(record)
        )

    return RecoveryPlan(RECOVERY_NOTHING, "no_pending_work")
