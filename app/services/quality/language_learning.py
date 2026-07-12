from __future__ import annotations

import re
from typing import Any, Callable

from app.schemas.quality import QualityIssue


def check_language_learning_quality(
    check: Callable[[dict[str, Any]], list[QualityIssue]], content: dict[str, Any]
) -> list[QualityIssue]:
    """Execute Language Learning content rules only."""
    return check(content)


def language_learning_text_fix_policy() -> str:
    return """
- Language Learning safety: never remove a learning-language term, a quoted learning term, or the correspondence between a learning expression and its pack-language explanation.
- Do not use an empty replacement. Keep all TTS language-tag boundaries valid.
""".strip()


_TAG = re.compile(r"\[[a-z]{2,3}(?:-[A-Za-z0-9]+)+\]", re.I)


def is_local_katakana_to_tagged_latin_reversal(before: str | None, after: str | None, display: str | None) -> bool:
    """Reject only the LL-specific local PC→[en-US]PC type reversal.

    This is deliberately structural, not a vocabulary deny-list.  It requires a
    katakana spoken fragment, a newly-tagged Latin display fragment, and a
    surrounding display field; whole learning phrases remain eligible for the
    normal tag-completion path.
    """
    if not before or not after or not display or not _TAG.search(after):
        return False
    spoken = _TAG.sub("", before)
    candidate = _TAG.sub("", after)
    if not re.search(r"[\u30a1-\u30fa]", spoken) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9 .+/#&-]*", candidate):
        return False
    if candidate not in display or len(display.strip()) <= len(candidate):
        return False
    return True


def learning_term_removal_reason(before: str | None, after: str | None) -> str | None:
    if after is None or not after.strip():
        return "replacement is empty"
    if before and _TAG.search(before) and not _TAG.search(after):
        return "language-tag boundaries would become invalid"
    # A Latin/CJK token disappearing from a text fix is a conservative signal
    # for a removed learning term.  Exact term matching remains in the caller.
    return None
