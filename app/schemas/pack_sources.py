"""sources.json（出典台帳）v0 スキーマ。docs/QUALITY_OPS_IMPLEMENTATION_PLAN.md §3 参照。

法的判定の断定はしない。宣言された出典とライセンスを機械的に保持するための台帳であり、
packs/<slug>/sources.json としてドラフト源と同梱する（旧パックでは不在でも error にならない）。
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class PackSourceRef(BaseModel):
    name: str = Field(..., min_length=1)
    url: str | None = None
    # SPDX 表記や自由記述を許容（"CC-BY-SA-4.0", "proprietary", "public-facts" 等）。
    license: str | None = None
    usage: Literal["facts_paraphrase", "structure_only", "verbatim", "none"] = "facts_paraphrase"
    verbatimAllowed: bool = False
    attributionRequired: bool = False
    notes: str = ""


class PackGeneratedBy(BaseModel):
    llm: str | None = None
    draftedBy: str | None = None
    date: str | None = None
    # 生成に使用した資料・会話が他にある場合の自由記述（future-proof）。
    extra: dict[str, Any] | None = None


class PackSourcesFile(BaseModel):
    schemaVersion: Literal[1] = 1
    sources: list[PackSourceRef] = Field(default_factory=list)
    generatedBy: PackGeneratedBy | None = None
