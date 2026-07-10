"""Language Learning（語学習得教材）生成戦略（Phase 2: 受け皿のみ）。

Phase 2 の責務:
- Strategy 受け皿を追加する。この段階では StandardStrategy と同じ処理（既存 generate_pack）を呼ぶ。
- 挙動は一切変わらない（生成結果は Standard と同一）。

Phase 3 以降で、Planner / Prompt / Document / Quiz / TTS / Quality の LL 専用処理を
ここへ順次移設し、語学教材固有の生成戦略とする。
"""

from app.schemas.request import GeneratePackRequest
from app.schemas.sokqa import GeneratePackResponse
from app.services.generation.strategy import GenerationStrategy
from app.services.pack_agent import generate_pack


class LanguageLearningStrategy(GenerationStrategy):
    def generate(self, request: GeneratePackRequest) -> GeneratePackResponse:
        return generate_pack(request)
