from __future__ import annotations

from typing import Any

from app.schemas.quality import QualityIssue


def check_common_structure(file_name: str, content: dict[str, Any], *, mode: str) -> list[QualityIssue]:
    """Shared structural gate.

    ``load_target_pack`` has already parsed the payload through the document or
    quiz schema before this point.  Consequently required fields, ids, choice
    counts, answer indices, language codes and TTS field shapes are rejected at
    that single shared gate.  Keep this hook explicit for additional
    schema-independent checks without putting semantic rules here.
    """
    del file_name, content, mode
    return []
