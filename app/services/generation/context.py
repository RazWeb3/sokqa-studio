from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.schemas.request import GeneratePackRequest
from app.services.generation.strategy import GenerationStrategy, resolve_generation_strategy


GenerationMode = Literal["standard", "language_learning"]


@dataclass(frozen=True)
class GenerationContext:
    """The generation-time decision carried through every downstream stage.

    Pack JSON is content, not an authority for selecting a processing mode.
    This context is created once from the resolved Strategy and is the only
    source for Language Learning-specific behavior.
    """

    mode: GenerationMode
    pack_language: str
    learning_language: str | None
    choice_language_mode: str | None
    tts_reading_mode: str
    structure_policy: str | None = None

    @property
    def is_language_learning(self) -> bool:
        return self.mode == "language_learning"

    @property
    def allow_language_tags(self) -> bool:
        return self.is_language_learning and self.tts_reading_mode == "multilingual"

    @classmethod
    def from_request(cls, request: GeneratePackRequest, *, tts_reading_mode: str) -> "GenerationContext":
        strategy = resolve_generation_strategy(request)
        return cls.from_strategy(strategy, request, tts_reading_mode=tts_reading_mode)

    @classmethod
    def from_strategy(
        cls, strategy: GenerationStrategy, request: GeneratePackRequest, *, tts_reading_mode: str
    ) -> "GenerationContext":
        plan = request.plan
        return cls(
            mode="language_learning" if strategy.is_language_learning else "standard",
            pack_language=plan.language,
            learning_language=plan.learningLanguage,
            choice_language_mode=getattr(plan, "choiceLanguageMode", None),
            tts_reading_mode=tts_reading_mode,
            structure_policy=plan.structurePolicy,
        )

    @classmethod
    def standard(cls) -> "GenerationContext":
        """Compatibility policy for existing persisted packs without mode metadata."""
        return cls(
            mode="standard",
            pack_language="ja",
            learning_language=None,
            choice_language_mode=None,
            tts_reading_mode="none",
        )
