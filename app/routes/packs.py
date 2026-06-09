from fastapi import APIRouter, HTTPException

from app.schemas.request import DeletePackRequest, DeletePackResponse, ImportPackRequest, ImportPackResponse
from app.services.pack_deletion import delete_pack_version
from app.services.pack_importer import import_pack_files
from app.services.pack_listing import list_generated_packs


router = APIRouter(tags=["packs"])


@router.get("/packs")
def list_packs(creatorId: str | None = None) -> dict:
    try:
        return {"items": list_generated_packs(creatorId)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/packs/import", response_model=ImportPackResponse)
def import_packs(request: ImportPackRequest) -> ImportPackResponse:
    try:
        return import_pack_files(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/packs/delete", response_model=DeletePackResponse)
def delete_pack(request: DeletePackRequest) -> DeletePackResponse:
    try:
        return delete_pack_version(request)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
