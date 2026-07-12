from __future__ import annotations

from dataclasses import dataclass

from app.services.generation.context import GenerationContext
from app.services.generation.strategy import GenerationStrategy


@dataclass(frozen=True)
class QualityContext:
    """Legacy quality-only adapter for callers that already hold a Strategy.

    Production generation uses ``GenerationContext``.  This adapter does not
    inspect pack content; it derives its mode solely from the supplied Strategy.
    """

    strategy: GenerationStrategy

    @property
    def is_language_learning(self) -> bool:
        return self.strategy.is_language_learning

    @property
    def mode(self) -> str:
        return "language_learning" if self.is_language_learning else "standard"

    @property
    def allow_language_tags(self) -> bool:
        return self.is_language_learning


ProcessingContext = GenerationContext | QualityContext
