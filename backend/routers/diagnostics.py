"""Sanitized support diagnostics."""

from fastapi import APIRouter

from services.project_diagnostics import build_project_diagnostics

router = APIRouter()


@router.get("/project-summary")
def project_summary():
    return build_project_diagnostics()
