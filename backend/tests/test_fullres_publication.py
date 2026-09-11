"""Publikacja dwuetapowa i odzyskiwanie — R1.4."""

from __future__ import annotations

import pytest

from services.scene_packages.fullres_publication import (
    ACTIVATION_STEPS,
    RECOVERY_NOTHING,
    RECOVERY_RESTART,
    RECOVERY_RESUME,
    RECOVERY_ROLLBACK,
    STATE_ACTIVE,
    STATE_BUILDING,
    STATE_CANDIDATE_READY,
    STATE_FAILED,
    STATE_VALIDATING,
    STEP_ARTIFACT,
    STEP_MANIFEST,
    STEP_SCENE,
    InvalidTransitionError,
    PublicationRecord,
    advance,
    mark_step,
    plan_recovery,
    remaining_steps,
)


def _ready(steps=()):
    record = PublicationRecord(state=STATE_CANDIDATE_READY)
    for step in steps:
        mark_step(record, step)
    return record


# --- przejścia stanów -----------------------------------------------------------------


def test_happy_path_goes_through_validation():
    record = PublicationRecord()
    advance(record, STATE_VALIDATING)
    advance(record, STATE_CANDIDATE_READY)
    advance(record, STATE_ACTIVE)
    assert record.state == STATE_ACTIVE


def test_cannot_skip_validation():
    """Bramka z §19 wymaga sprawdzenia KAŻDEGO derywatu — skrót nie może istnieć."""
    record = PublicationRecord()
    with pytest.raises(InvalidTransitionError):
        advance(record, STATE_ACTIVE)
    with pytest.raises(InvalidTransitionError):
        advance(record, STATE_CANDIDATE_READY)


def test_active_is_terminal():
    record = PublicationRecord(state=STATE_ACTIVE)
    with pytest.raises(InvalidTransitionError):
        advance(record, STATE_VALIDATING)


def test_failed_build_can_be_restarted_and_clears_progress():
    record = PublicationRecord(state=STATE_VALIDATING)
    mark_step(record, STEP_ARTIFACT)
    advance(record, STATE_FAILED)
    record.error = "brak pamięci"
    advance(record, STATE_BUILDING)
    assert record.steps_done == []
    assert record.error is None


def test_unknown_step_is_rejected():
    with pytest.raises(ValueError):
        mark_step(PublicationRecord(), "cokolwiek")


def test_steps_are_idempotent_and_ordered():
    record = PublicationRecord()
    mark_step(record, STEP_ARTIFACT)
    mark_step(record, STEP_ARTIFACT)
    assert record.steps_done == [STEP_ARTIFACT]
    assert remaining_steps(record) == [STEP_MANIFEST, STEP_SCENE]


def test_activation_order_puts_artifact_before_the_pointers():
    """Manifest wskazuje plik, a scene.json manifest — odwrotna kolejność
    tworzyłaby wpis wskazujący na coś, czego jeszcze nie ma."""
    assert ACTIVATION_STEPS == (STEP_ARTIFACT, STEP_MANIFEST, STEP_SCENE)


def test_record_round_trips_through_dict():
    record = PublicationRecord(state=STATE_CANDIDATE_READY, payload={"scene_id": "s1"})
    mark_step(record, STEP_ARTIFACT)
    restored = PublicationRecord.from_dict(record.as_dict())
    assert restored.state == STATE_CANDIDATE_READY
    assert restored.steps_done == [STEP_ARTIFACT]
    assert restored.payload == {"scene_id": "s1"}


def test_record_from_empty_dict_is_a_fresh_build():
    assert PublicationRecord.from_dict(None).state == STATE_BUILDING


# --- odzyskiwanie ---------------------------------------------------------------------


def test_completed_activation_needs_no_recovery():
    record = _ready(ACTIVATION_STEPS)
    advance(record, STATE_ACTIVE)
    plan = plan_recovery(record, artifact_present=True, source_still_matches=True)
    assert plan.action == RECOVERY_NOTHING


def test_interrupted_between_manifest_and_scene_resumes():
    """Dokładnie ten przypadek, dla którego istnieje zapis postępu."""
    record = _ready([STEP_ARTIFACT, STEP_MANIFEST])
    plan = plan_recovery(record, artifact_present=True, source_still_matches=True)
    assert plan.action == RECOVERY_RESUME
    assert plan.steps == [STEP_SCENE]


def test_interrupted_right_after_the_file_resumes_the_rest():
    record = _ready([STEP_ARTIFACT])
    plan = plan_recovery(record, artifact_present=True, source_still_matches=True)
    assert plan.action == RECOVERY_RESUME
    assert plan.steps == [STEP_MANIFEST, STEP_SCENE]


def test_missing_artifact_means_restart_not_resume():
    record = _ready([STEP_ARTIFACT, STEP_MANIFEST])
    plan = plan_recovery(record, artifact_present=False, source_still_matches=True)
    assert plan.action == RECOVERY_RESTART
    assert plan.reason == "artifact_missing"


def test_source_changed_since_build_rolls_back():
    """Aplikacja mogła być zamknięta tydzień, a scena w tym czasie zrelinkowana."""
    record = _ready([STEP_ARTIFACT])
    plan = plan_recovery(record, artifact_present=True, source_still_matches=False)
    assert plan.action == RECOVERY_ROLLBACK


def test_interruption_before_validation_restarts_the_build():
    """Plik może być ucięty, a nie umiemy tego stwierdzić inaczej niż walidacją."""
    for state in (STATE_BUILDING, STATE_VALIDATING):
        plan = plan_recovery(
            PublicationRecord(state=state), artifact_present=True, source_still_matches=True
        )
        assert plan.action == RECOVERY_RESTART
        assert plan.reason == f"interrupted_in_{state}"


def test_active_state_with_missing_steps_is_still_resumed():
    """Stan zapisany jako aktywny, ale bez kompletu kroków, to niespójność do naprawy."""
    record = PublicationRecord(state=STATE_ACTIVE)
    mark_step(record, STEP_ARTIFACT)
    plan = plan_recovery(record, artifact_present=True, source_still_matches=True)
    assert plan.action == RECOVERY_RESUME
    assert plan.steps == [STEP_MANIFEST, STEP_SCENE]
