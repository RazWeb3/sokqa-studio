from __future__ import annotations

from typing import Any, Callable

from app.schemas.quality import QualityIssue


def check_standard_quality(
    check: Callable[[dict[str, Any]], list[QualityIssue]], content: dict[str, Any]
) -> list[QualityIssue]:
    """Execute Standard content rules only."""
    return check(content)
