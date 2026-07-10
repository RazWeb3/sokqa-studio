"""Generation Strategy 入口層（Phase 2: 判定統合・戦略選択）。

Phase 1 の責務（受け皿）に加え、Phase 2 では:
- 語学判定の2系統（系統A: 外国語学習 / 系統B: 日本語学習）を1つの判定へ統合する。
- resolve_generation_strategy が統合判定に基づき戦略を選択する。

Phase 2 時点では LanguageLearningStrategy も既存 generate_pack を委譲するため、生成結果は変わらない。
Planner / Prompt 等の LL 専用処理の移設は Phase 3 以降で行う。
"""

from abc import ABC, abstractmethod

from app.schemas.request import GeneratePackRequest
from app.schemas.sokqa import CoursePlan, GeneratePackResponse


def _base_language(language: str | None) -> str:
    return (language or "").split("-")[0].strip().lower()


def is_language_learning_plan(plan: CoursePlan) -> bool:
    """語学学習教材かどうかの統合判定（系統A/B を1つに集約）。

    系統A: learningLanguage あり かつ learningLanguage != packLanguage（外国語学習）
    系統B: structurePolicy == "japanese_learning" かつ packLanguage != "ja"（日本語学習）

    Phase 3 以降、各モジュール内の分散した if language learning 判定は、
    この関数（または is_language_learning_request）へ集約する。
    """
    pack_language = _base_language(plan.language)
    learning_language = _base_language(plan.learningLanguage)

    # 系統A
    if learning_language and pack_language and learning_language != pack_language:
        return True

    # 系統B
    if plan.structurePolicy == "japanese_learning" and pack_language and pack_language != "ja":
        return True

    return False


def is_language_learning_request(request: GeneratePackRequest) -> bool:
    """GeneratePackRequest から統合判定を実行する（plan に委譲）。"""
    return is_language_learning_plan(request.plan)


class GenerationStrategy(ABC):
    """教材生成戦略の共通インターフェース。

    Standard / Language Learning の両戦略が実装する。
    """

    @abstractmethod
    def generate(self, request: GeneratePackRequest) -> GeneratePackResponse:
        raise NotImplementedError


def resolve_generation_strategy(request: GeneratePackRequest) -> GenerationStrategy:
    """リクエストから生成戦略を決定する。

    Phase 2: 統合判定に基づき戦略を選択。
    Language Learning と判定されれば LanguageLearningStrategy、それ以外は StandardStrategy。
    """
    from app.services.generation.strategies.language_learning import LanguageLearningStrategy
    from app.services.generation.strategies.standard import StandardStrategy

    if is_language_learning_request(request):
        return LanguageLearningStrategy()
    return StandardStrategy()
