"""CRUD annotations (scene pixel coords) — per scene."""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from db.storage import (
    load_json,
    load_scene_json,
    mutate_scene_json,
    save_scene_json,
    project_exists,
)
from models.annotation import Annotation, AnnotationCreate, AnnotationUpdate
from services.attribute_engine import enrich_source_annotation, recompute_scene_attributes
from services.scene_packages.working_view import lock_working_view

router = APIRouter()


def _get_annotations(project_id: str, scene_id: str) -> list[dict]:
    return load_scene_json(project_id, scene_id, "annotations", default=[])


def _update_annotation_count(project_id: str, scene_id: str, count: int) -> None:
    scene_data = load_scene_json(project_id, scene_id, "scene")
    if scene_data:
        scene_data["annotation_count"] = count
        save_scene_json(project_id, scene_id, "scene", scene_data)


def _project_author_email(project_id: str) -> str | None:
    project_data = load_json(project_id, "project", default={})
    profile = project_data.get("profile") or {}
    return profile.get("labeling_author_email") or None


# Handlery sa celowo synchroniczne (`def`, nie `async def`): wykonuja blokujace I/O JSON
# i przeliczanie atrybutow, wiec FastAPI uruchamia je w swojej puli watkow. Jako `async def`
# blokowaly petle zdarzen backendu na czas calego zadania — przy scenie DOTA z 10 206
# adnotacjami bylo to ~2,2 s, w ktorych zadne inne zadanie HTTP nie bylo obslugiwane
# (DESIGN_DECISIONS.md, performance-roadmap P0.3 — ten router zostal wtedy pominiety).
@router.get("/")
def list_annotations(project_id: str, scene_id: str):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    recompute_scene_attributes(project_id, scene_id)
    return _get_annotations(project_id, scene_id)


@router.post("/")
def create_annotation(project_id: str, scene_id: str, body: AnnotationCreate):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    if body.bbox is None:
        raise HTTPException(400, "Annotation bbox could not be derived from geometry")

    author_email = _project_author_email(project_id)
    ann = Annotation(
        id=uuid.uuid4().hex[:10],
        class_id=body.class_id,
        geometry_type=body.geometry_type,
        bbox=body.bbox,
        rotated_bbox=body.rotated_bbox,
        polygon_scene_px=body.polygon_scene_px,
        front_edge_scene_px=body.front_edge_scene_px,
        front_vector_scene_px=body.front_vector_scene_px,
        orientation_angle_deg=body.orientation_angle_deg,
        is_negative=body.is_negative,
        scene_id=scene_id,
        annotation_source="manual",
        annotator_email=author_email,
        created_by=author_email,
        updated_by=author_email,
    )
    ann_data = ann.model_dump()
    ann_data["source_annotation_id"] = ann_data["id"]
    ann_data = enrich_source_annotation(project_id, scene_id, ann_data)
    anns, _revision = mutate_scene_json(
        project_id,
        scene_id,
        "annotations",
        lambda current: [*current, ann_data],
        default=[],
    )
    _update_annotation_count(project_id, scene_id, len(anns))
    lock_working_view(project_id, scene_id, "first_annotation")
    return ann_data


@router.put("/{ann_id}")
def update_annotation(project_id: str, scene_id: str, ann_id: str, body: AnnotationUpdate):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")

    updated_annotation: dict | None = None

    def apply_update(anns: list[dict]) -> list[dict]:
        nonlocal updated_annotation
        for index, a in enumerate(anns):
            if a["id"] != ann_id:
                continue
            author_email = _project_author_email(project_id)
            updates = body.model_dump(exclude_unset=True)
            geometry_keys = {
                "geometry_type",
                "bbox",
                "rotated_bbox",
                "polygon_scene_px",
                "front_edge_scene_px",
                "front_vector_scene_px",
                "orientation_angle_deg",
            }
            if geometry_keys.intersection(body.model_fields_set):
                derived_updates = body.model_dump()
                for key in geometry_keys:
                    if key in body.model_fields_set or derived_updates.get(key) is not None:
                        updates[key] = derived_updates.get(key)
            for key in (
                "class_id",
                "geometry_type",
                "bbox",
                "rotated_bbox",
                "polygon_scene_px",
                "front_edge_scene_px",
                "front_vector_scene_px",
                "orientation_angle_deg",
                "is_negative",
            ):
                if key in updates:
                    a[key] = updates[key]
            if author_email:
                a["updated_by"] = author_email
            a["updated_at"] = datetime.now(timezone.utc).isoformat()
            a = enrich_source_annotation(project_id, scene_id, a)
            anns[index] = a
            updated_annotation = a
            return anns
        raise HTTPException(404, "Annotation not found")

    mutate_scene_json(
        project_id,
        scene_id,
        "annotations",
        apply_update,
        default=[],
    )
    return updated_annotation


@router.delete("/{ann_id}")
def delete_annotation(project_id: str, scene_id: str, ann_id: str):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")

    def apply_delete(anns: list[dict]) -> list[dict]:
        new_anns = [a for a in anns if a["id"] != ann_id]
        if len(new_anns) == len(anns):
            raise HTTPException(404, "Annotation not found")
        return new_anns

    new_anns, _revision = mutate_scene_json(
        project_id,
        scene_id,
        "annotations",
        apply_delete,
        default=[],
    )
    _update_annotation_count(project_id, scene_id, len(new_anns))
    return {"status": "deleted"}
