from fastapi import APIRouter, HTTPException

from app.schemas.request import GeneratePackRequest
from app.schemas.sokqa import GeneratePackResponse
from app.services.generation.strategy import resolve_generation_strategy


router = APIRouter(tags=["generation"])


@router.post("/generate-pack", response_model=GeneratePackResponse)
def generate(request: GeneratePackRequest) -> GeneratePackResponse:
    try:
        # Phase 1: 入口のみ Strategy 経由へ切り替え。内部ロジックは既存 generate_pack を維持。
        strategy = resolve_generation_strategy(request)
        return strategy.generate(request)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "message": str(exc),
                "hint": "壊れたフォールバック文書を保存しないため、生成を中断しました。少し時間を置いて再生成するか、追加条件・資料量・生成数を減らして再試行してください。",
            },
        ) from exc
