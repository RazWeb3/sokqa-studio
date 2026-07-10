"""Standard（通常教材）生成戦略（Phase 1: 既存処理の委譲のみ）。

Phase 1 の責務:
- 既存の generate_pack をそのまま呼び出す。
- 挙動を一切変更しない（Standard = 現在の動作）。

Language Learning 専用処理の切り出し・集約は Phase 2 以降で行い、
ここから分岐する形にする（Phase 1 は純粋な委譲）。
"""

from app.schemas.request import GeneratePackRequest
from app.schemas.sokqa import GeneratePackResponse
from app.services.generation.strategy import GenerationStrategy
from app.services.pack_agent import generate_pack


class StandardStrategy(GenerationStrategy):
    def generate(self, request: GeneratePackRequest) -> GeneratePackResponse:
        return generate_pack(request)
