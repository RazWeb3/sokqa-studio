from dataclasses import dataclass
from typing import Literal


GenerationSource = Literal["gemini", "mock"]


@dataclass
class GenerationStatus:
    source: GenerationSource
    message: str


_events: list[GenerationStatus] = []


def record_generation_source(source: GenerationSource, message: str) -> None:
    _events.append(GenerationStatus(source=source, message=message))


def pop_generation_events() -> list[GenerationStatus]:
    events = list(_events)
    _events.clear()
    return events
