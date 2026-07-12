"""Parse language tags embedded in TTS-only text fields."""

from __future__ import annotations

import re
from dataclasses import dataclass


_LANGUAGE_TAG = re.compile(r"\[([a-z]{2,3}-[A-Z]{2})\]")


@dataclass(frozen=True)
class TtsLanguageSegment:
    text: str
    language_code: str


def parse_tts_language_segments(text: str, default_language_code: str) -> list[TtsLanguageSegment]:
    """Split tagged TTS text while retaining the active language between tags.

    Tags are control syntax and are therefore never sent to a speech provider.
    Empty spans are ignored, so adjacent tags simply change the active voice.
    """
    active_language = default_language_code
    cursor = 0
    segments: list[TtsLanguageSegment] = []
    for match in _LANGUAGE_TAG.finditer(text):
        _append_segment(segments, text[cursor : match.start()], active_language)
        active_language = match.group(1)
        cursor = match.end()
    _append_segment(segments, text[cursor:], active_language)
    return segments


def speech_character_count(text: str) -> int:
    """Return billable characters, excluding Sokqa's language-control tags."""
    return len(_LANGUAGE_TAG.sub("", text))


def speech_segment_count(text: str, default_language_code: str) -> int:
    return len(parse_tts_language_segments(text, default_language_code))


def _append_segment(segments: list[TtsLanguageSegment], text: str, language_code: str) -> None:
    if text:
        segments.append(TtsLanguageSegment(text=text, language_code=language_code))
