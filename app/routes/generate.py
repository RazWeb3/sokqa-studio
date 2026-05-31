from fastapi import APIRouter

from app.schemas.request import GeneratePackRequest
from app.schemas.sokqa import GeneratePackResponse
from app.services.pack_agent import generate_pack


router = APIRouter(tags=["generation"])


@router.post("/generate-pack", response_model=GeneratePackResponse)
def generate(request: GeneratePackRequest) -> GeneratePackResponse:
    return generate_pack(request)
