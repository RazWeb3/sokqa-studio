from __future__ import annotations

from typing import Any, Callable

from app.schemas.quality import QualityIssue
from app.services.quality.common import check_common_structure
from app.services.quality.context import QualityContext
from app.services.quality.language_learning import check_language_learning_quality
from app.services.quality.standard import check_standard_quality


def check_quality(
    context: QualityContext,
    file_name: str,
    content: dict[str, Any],
    *,
    mode: str,
    standard_check: Callable[[dict[str, Any]], list[QualityIssue]],
    language_learning_check: Callable[[dict[str, Any]], list[QualityIssue]],
) -> list[QualityIssue]:
    """Run shared structure plus exactly one content-quality branch."""
    issues = check_common_structure(file_name, content, mode=mode)
    if context.is_language_learning:
        return issues + check_language_learning_quality(language_learning_check, content)
    return issues + check_standard_quality(standard_check, content)
