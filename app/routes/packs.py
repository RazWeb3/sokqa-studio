from fastapi import APIRouter, HTTPException

from app.services.pack_listing import list_generated_packs


router = APIRouter(tags=["packs"])


@router.get("/packs")
def list_packs(creatorId: str | None = None) -> dict:
    try:
        return {"items": list_generated_packs(creatorId)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
