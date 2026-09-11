"""AI-assist tools that produce proposals into the shared staging layer.

SAM click-to-box and template matching support EO and SAR scenes.
Proposals land in versioned assist sessions and are accepted/rejected through
the existing predictions endpoints.
"""

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from db.storage import load_json, load_scene_json, project_exists
from models.assistance import AssistanceProposal
from services.assistance_sessions import create_session, get_active_session, write_session
from services.predictor import resolve_device
from services.sam_assist import (
    SamAssistError,
    mock_enabled,
    resolve_sam_checkpoint,
    sam_click_proposal,
    sam_model_info,
    sam_text_proposals,
)

router = APIRouter()


class SamClickRequest(BaseModel):
    x: float
    y: float
    positive_points: list[list[float]] = []
    negative_points: list[list[float]] = []
    geometry_type: str = "bbox"  # bbox | rotated_bbox
    class_id: int | None = None
    # SB3: rozmiar odniesienia (px sceny [x0,y0,x1,y1]) z ostatniego boxa / zaznaczonej
    # adnotacji — zasila prior rozmiaru SB2, pierwszeństwo przed mediana klasy.
    size_prior_bbox: list[float] | None = None


class FindSimilarRequest(BaseModel):
    exemplar_bbox: list[float]  # [x0, y0, x1, y1] scene px
    class_id: int | None = None
    threshold: float = Field(default=0.70, ge=0.20, le=0.99)
    scale_tolerance: float = Field(default=0.10, ge=0.0, le=0.20)
    rotation_tolerance_deg: int = Field(default=20, ge=0, le=45)
    use_edges: bool = True
    geometry_type: str = "bbox"
    exemplar_rotated_bbox: dict[str, float] | None = None
    exemplar_front_vector: list[float] | None = None
    #: Ksztalt wzorca WIERNY MAPIE. Bez niego przeniesienie musialoby odtwarzac
    #: ramke z `cx/cy/w/h/angle`, czyli z prostokata pikselowego — a to jest
    #: dokladnie zrodlo skosu, ktory ta sciezka miala usunac.
    exemplar_polygon_scene_px: list[list[float]] | None = None
    search_mode: Literal["local", "viewport", "scene"] = "local"
    search_bbox: list[float] | None = None  # [x0, y0, x1, y1] scene px
    engine: Literal["template", "dino"] = "template"  # NCC szablonowy vs few-shot DINO
    extra_exemplar_bboxes: list[list[float]] | None = None  # dodatkowe wzorce → prototyp DINO


class SamTextRequest(BaseModel):
    class_id: int  # klasa nadawana zaakceptowanym propozycjom
    search_bbox: list[float]  # okno wyszukiwania [x0, y0, x1, y1] px sceny (widok / narysowany box)
    # Prompt tekstowy dla SAM3 (open-vocab, angielski). Osobno od klasy: nazwy klas bywają
    # skrótami/„pojazd_transportowy". Puste → fallback do nazwy klasy.
    text: str | None = None
    geometry_type: str = "bbox"  # bbox | rotated_bbox
    # 0.25: instancje na zobrazowaniach EO/SAR (zwłaszcza poglądowych) mają umiarkowane
    # pewności — próg 0.5 odcinał za dużo. Wartość robocza, do kalibracji na natywnych kaflach.
    confidence_threshold: float = Field(default=0.25, ge=0.0, le=1.0)


def _scene_modality(project_id: str, scene_id: str) -> str | None:
    manifest = load_scene_json(project_id, scene_id, "scene_manifest", default={})
    if manifest.get("modality"):
        return manifest.get("modality")
    scene = load_scene_json(project_id, scene_id, "scene", default={})
    if scene.get("modality"):
        return scene.get("modality")
    project = load_json(project_id, "project", default={})
    return (project.get("profile") or {}).get("modality")


@router.post("/sam/click")
async def sam_click(project_id: str, scene_id: str, body: SamClickRequest):
    """One SAM click → proposal appended to the active sam_click session."""
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")

    modality = _scene_modality(project_id, scene_id)

    if body.geometry_type not in ("bbox", "rotated_bbox"):
        raise HTTPException(400, "geometry_type must be 'bbox' or 'rotated_bbox'")

    if body.class_id is not None:
        classes = load_json(project_id, "classes", default=[])
        if body.class_id not in {int(c["id"]) for c in classes}:
            raise HTTPException(400, "class_id is not a project class")

    try:
        config = load_json(project_id, "prediction_config", default={})
        checkpoint = resolve_sam_checkpoint(
            config.get("sam_checkpoint") or None,
            config.get("sam_models_dir") or None,
        )
        device = resolve_device()
        if not mock_enabled() and not checkpoint:
            raise SamAssistError("No SAM checkpoint configured or bundled")
        if not mock_enabled() and checkpoint:
            # Czytelny blad konfiguracji (np. SAM3 bez CLIP offline) zamiast mylacego 422
            # "znaleziono 0" albo sieciowego zawisu przy budowie modelu.
            info = sam_model_info(checkpoint)
            if not info["supported"]:
                raise HTTPException(400, info["reason"] or f"Unsupported SAM checkpoint: {info['name']}")
        proposal_payload = await run_in_threadpool(
            sam_click_proposal,
            project_id,
            scene_id,
            click_x=body.x,
            click_y=body.y,
            positive_points=body.positive_points,
            negative_points=body.negative_points,
            geometry_type=body.geometry_type,
            class_id=body.class_id,
            checkpoint=checkpoint,
            device=device,
            modality=modality,
            size_prior_bbox=body.size_prior_bbox,
        )
    except SamAssistError as exc:
        raise HTTPException(422, str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc

    proposal = AssistanceProposal(**proposal_payload)

    # Append to the active sam_click session, or create one.
    session = get_active_session(project_id, scene_id)
    if session is None or session.source_tool != "sam_click" or session.status == "resolved":
        session = create_session(
            project_id, scene_id, "sam_click", [proposal],
            device=device, model_name=proposal.model_name, model_sha=proposal.model_sha256,
            working_grid_uid=proposal.working_grid_uid,
        )
    else:
        proposal.session_id = session.session_id
        session.proposals.append(proposal)
        write_session(project_id, scene_id, session)

    return {
        "session_id": session.session_id,
        "proposal": proposal.to_legacy_prediction(),
        "rotated_bbox": proposal.rotated_bbox,
        "source_window": proposal.source_window,
        "needs_front_direction": proposal.geometry_type == "rotated_bbox",
    }


@router.post("/sam/text")
async def sam_text(project_id: str, scene_id: str, body: SamTextRequest):
    """SAM3 text prompt over the current view → instance proposals into a sam_text session."""
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    if body.geometry_type not in ("bbox", "rotated_bbox"):
        raise HTTPException(400, "geometry_type must be 'bbox' or 'rotated_bbox'")

    classes = load_json(project_id, "classes", default=[])
    class_name = next((c.get("name") for c in classes if int(c["id"]) == body.class_id), None)
    if class_name is None:
        raise HTTPException(400, "class_id is not a project class")
    # Prompt: jawny tekst użytkownika, a jako fallback nazwa klasy (może być skrótem/„_").
    prompt = (body.text or "").strip() or str(class_name)

    modality = _scene_modality(project_id, scene_id)
    config = load_json(project_id, "prediction_config", default={})
    checkpoint = resolve_sam_checkpoint(
        config.get("sam_checkpoint") or None,
        config.get("sam_models_dir") or None,
    )
    device = resolve_device()

    # Bramkowanie: tryb tekstowy wymaga SAM3 (z CLIP). Czytelny powód zamiast „Network Error".
    if not mock_enabled():
        if not checkpoint:
            raise HTTPException(400, "No SAM checkpoint configured or bundled")
        info = sam_model_info(checkpoint)
        if info.get("family") != "sam3":
            raise HTTPException(400, "Text mode requires a SAM3 checkpoint (e.g. sam3.pt)")
        if not info["supported"]:
            raise HTTPException(400, info["reason"] or "SAM3 text mode is unavailable in this runtime")

    try:
        payloads = await run_in_threadpool(
            sam_text_proposals, project_id, scene_id,
            class_id=body.class_id,
            prompt=prompt,
            search_bbox=body.search_bbox,
            geometry_type=body.geometry_type,
            checkpoint=checkpoint,
            device=device,
            confidence_threshold=body.confidence_threshold,
            modality=modality,
        )
    except SamAssistError as exc:
        raise HTTPException(422, str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc

    proposals = [AssistanceProposal(**payload) for payload in payloads]
    run_params = {"class_id": body.class_id, "confidence_threshold": body.confidence_threshold}
    session = get_active_session(project_id, scene_id)
    if (
        session is None
        or session.source_tool != "sam_text"
        or session.status == "resolved"
        or session.params != run_params
    ):
        session = create_session(
            project_id, scene_id, "sam_text", proposals,
            device=device, model_name=(proposals[0].model_name if proposals else None),
            working_grid_uid=(proposals[0].working_grid_uid if proposals else None),
            params=run_params,
        )
    else:
        for proposal in proposals:
            proposal.session_id = session.session_id
        session.proposals.extend(proposals)
        write_session(project_id, scene_id, session)

    return {
        "session_id": session.session_id,
        "found": len(proposals),
        "proposals": [p.to_legacy_prediction() for p in proposals],
    }


@router.post("/exemplar/find-similar")
async def exemplar_find_similar(project_id: str, scene_id: str, body: FindSimilarRequest):
    """Find similar objects with template matching on EO or SAR data."""
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    if body.class_id is not None:
        classes = load_json(project_id, "classes", default=[])
        if body.class_id not in {int(c["id"]) for c in classes}:
            raise HTTPException(400, "class_id is not a project class")

    from services.exemplar_assist import ExemplarError, transfer_exemplar_geometry

    modality = _scene_modality(project_id, scene_id)
    source_tool = "exemplar_dino" if body.engine == "dino" else "exemplar"

    if body.search_mode == "viewport" and (body.search_bbox is None or len(body.search_bbox) != 4):
        raise HTTPException(400, "search_bbox is required for viewport search")
    device = resolve_device()  # potrzebne juz w galezi DINO (backbone na GPU, nie CPU)
    try:
        if body.engine == "dino":
            from services.dino_exemplar_assist import DinoFeatureBackend, find_similar_dino
            from services.embedding_backbone import dino_runtime_available

            config = load_json(project_id, "prediction_config", default={})
            dino_dir = config.get("dino_models_dir") or None
            dino_ckpt = config.get("dino_checkpoint") or None
            if not dino_runtime_available(dino_ckpt, dino_dir):
                raise HTTPException(
                    400,
                    "Backbone DINO niedostępny - wskaż wagi w panelu Predykcja "
                    "(albo umieść je w MODELS_ROOT/dino, np. dinov3_vitl16_…sat493m.pth).",
                )
            backbone = DinoFeatureBackend(preferred=dino_ckpt, models_dir=dino_dir, device=device)
            exemplars = [body.exemplar_bbox, *(body.extra_exemplar_bboxes or [])]
            # DINO honoruje search_bbox (viewport); local → okolica egzemplarza w serwisie.
            search_bbox = body.search_bbox if body.search_mode != "local" else None
            payloads = await run_in_threadpool(
                find_similar_dino, project_id, scene_id,
                exemplar_bboxes=exemplars,
                class_id=body.class_id,
                backbone=backbone,
                threshold=body.threshold,
                search_bbox=search_bbox,
                modality=modality,
            )
            model_name = backbone.name
        else:
            from services.exemplar_assist import find_similar

            payloads = await run_in_threadpool(
                find_similar, project_id, scene_id,
                exemplar_bbox=body.exemplar_bbox,
                class_id=body.class_id,
                threshold=body.threshold,
                search_mode=body.search_mode,
                search_bbox=body.search_bbox,
                modality=modality,
                scale_tolerance=body.scale_tolerance,
                rotation_tolerance_deg=body.rotation_tolerance_deg,
                use_edges=body.use_edges,
            )
            model_name = "template_ncc_v2"
        payloads = transfer_exemplar_geometry(
            payloads,
            exemplar_bbox=body.exemplar_bbox,
            geometry_type=body.geometry_type,
            exemplar_rotated_bbox=body.exemplar_rotated_bbox,
            exemplar_front_vector=body.exemplar_front_vector,
            exemplar_polygon_scene_px=body.exemplar_polygon_scene_px,
            project_id=project_id,
            scene_id=scene_id,
        )
    except (ExemplarError, SamAssistError) as exc:
        raise HTTPException(422, str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc

    proposals = [AssistanceProposal(**payload) for payload in payloads]
    run_params = {
        "engine": body.engine,
        "search_mode": body.search_mode,
        "threshold": body.threshold,
        "scale_tolerance": body.scale_tolerance,
        "rotation_tolerance_deg": body.rotation_tolerance_deg,
        "use_edges": body.use_edges,
    }
    session = get_active_session(project_id, scene_id)
    if (
        session is None
        or session.source_tool != source_tool
        or session.status == "resolved"
        or session.params != run_params
    ):
        session = create_session(
            project_id, scene_id, source_tool, proposals,
            device=device, model_name=model_name,
            working_grid_uid=(proposals[0].working_grid_uid if proposals else None),
            params=run_params,
        )
    else:
        for proposal in proposals:
            proposal.session_id = session.session_id
        session.proposals.extend(proposals)
        write_session(project_id, scene_id, session)

    return {
        "session_id": session.session_id,
        "found": len(proposals),
        "proposals": [p.to_legacy_prediction() for p in proposals],
    }


@router.post("/proposals/{proposal_id}/rotate-front")
@router.post("/proposals/{proposal_id}/flip-front", include_in_schema=False)
async def rotate_proposal_front(project_id: str, scene_id: str, proposal_id: str):
    """Rotate the front direction of a pending oriented-box proposal by 90 degrees."""
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")

    session = get_active_session(project_id, scene_id)
    if session is None:
        raise HTTPException(404, "No active assist session")
    proposal = next((p for p in session.proposals if p.proposal_id == proposal_id), None)
    if proposal is None:
        raise HTTPException(404, "Proposal not found")
    if proposal.geometry_type != "rotated_bbox" or not proposal.rotated_bbox:
        raise HTTPException(400, "Proposal has no oriented box to rotate")

    from services.sam_assist import default_front_vector, front_vector_from_polygon
    from services.sensor_geometry import rotated_bbox_from_polygon_px

    polygon = proposal.polygon_scene_px
    if polygon and len(polygon) >= 4:
        # Obrot o 90 stopni to PRZESTAWIENIE WIERZCHOLKOW poligonu o jeden, nie obrot
        # wektora w pikselach. Na scenach niekonforemnych obrot pikselowy nie jest
        # obrotem o 90 stopni NA MAPIE, wiec cztery klikniecia nie wracaly do punktu
        # wyjscia, a strzalka przestawala pokrywac sie z bokiem ramki. Przy przestawieniu
        # przod zawsze biegnie wzdluz krawedzi p0->p1, a sama ramka sie nie rusza.
        rotated_polygon = polygon[1:4] + polygon[:1]
        proposal.polygon_scene_px = rotated_polygon
        summary = rotated_bbox_from_polygon_px(rotated_polygon)
        if summary is not None:
            proposal.rotated_bbox = summary
        proposal.front_vector_scene_px = (
            front_vector_from_polygon(rotated_polygon) or proposal.front_vector_scene_px
        )
    else:
        vector = proposal.front_vector_scene_px or default_front_vector(proposal.rotated_bbox)
        # Bez poligonu piksel jest mapa. Os Y rosnie w dol, wiec (-y, x) przesuwa kierunek
        # zgodnie z ruchem wskazowek w czterech deterministycznych krokach.
        proposal.front_vector_scene_px = [-vector[1], vector[0]]
    write_session(project_id, scene_id, session)
    return {"proposal": proposal.to_legacy_prediction()}


# Backward-compatible Python symbol for older integrations.
flip_proposal_front = rotate_proposal_front
