from __future__ import annotations

from dataclasses import dataclass

from app.services.generation.strategy import GenerationStrategy


@dataclass(frozen=True)
class QualityContext:
    """The already-resolved generation strategy for a quality operation."""

    strategy: GenerationStrategy

    @property
    def is_language_learning(self) -> bool:
        return self.strategy.is_language_learning
