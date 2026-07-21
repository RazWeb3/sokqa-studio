"""Voice-safe sentence planning for Cloud Text-to-Speech."""

from __future__ import annotations

_SENTENCE_ENDINGS = frozenset("。！？!?")
# Chirp 3 HD rejects some long, complex sentences before the general 5,000-byte
# request limit. This is a warning target, not a claimed provider limit.
CHIRP3_WARNING_SENTENCE_CHARS = 80


def find_long_sentences(text: str, min_chars: int = CHIRP3_WARNING_SENTENCE_CHARS) -> list[str]:
    """Return sentences likely to require Chirp 3 HD chunking."""
    return [sentence for sentence in _sentences(text) if len(sentence) >= min_chars]


def is_sentence_too_long_error(error: BaseException | str) -> bool:
    message = str(error).lower()
    return "sentences that are too long" in message or "sentence is too long" in message


def _sentences(text: str) -> list[str]:
    sentences: list[str] = []
    start = 0
    for index, char in enumerate(text):
        if char in _SENTENCE_ENDINGS:
            sentences.append(text[start : index + 1])
            start = index + 1
    if start < len(text):
        sentences.append(text[start:])
    return [sentence for sentence in sentences if sentence]

