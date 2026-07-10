"""生成コンテンツの内容検証（専用バリデータ）。

生成パイプライン（pack_agent）の末尾で呼び出し、LLM が生成した英語スパンが
「中途切断（語脱落・ハイフン中途）」していないかを決定論的に検知する。

検知対象の破損パターン（実データで確認済み）:
- doc_04 doc-13: "I'm having trouble connecting to the."  (Wi-Fi 抜けによる語脱落)
- doc_05 doc-18: "Could you tell me more about the"       (料理名等の脱落)
- doc_01 doc-30: "step- directions"                        (ハイフン中途)

このバリデータは「検知・報告」のみを行い、修正は既存の品質チェックフローに委ねる。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# 多言語パックの [en-US]...[ja-JP] スイッチタグ列。
_LANGUAGE_SPAN_RE = re.compile(r"\[en-US\](.*?)\[ja-JP\]", re.DOTALL)
# 英語スパン内に日本語文字（ひらがな/カタカナ/漢字）が混入しているか。
_JAPANESE_CHAR_RE = re.compile(r"[぀-ヿ一-鿿]")
# ハイフン中途: 英語トークンがハイフンで終わる（"step-" 等）。
# 実データでは "step- directions" のようにハイフン直後にスペースが続く形もあるため、
# 行末またはハイフン+空白のいずれにもマッチさせる。
_TRAILING_HYPHEN_RE = re.compile(r"[A-Za-z]-(?:\s+|$)")
# 中途切断: 文が前置詞/冠詞/助動詞等で終わる（"the." / "about the" 等）。
_TRAILING_FRAGMENT_RE = re.compile(
    r"\b(the|to|a|an|of|in|on|for|with|from|about|my|your|is|are|was|were|do|does|did|have|has|had)\.?$",
    re.IGNORECASE,
)
# 学習者向け本文（text フィールド）内の独立した純英語ブロック。
# 日本語と混在せず、かつ一定長以上の連続するラテン文字列。
_STANDALONE_ENGLISH_RE = re.compile(r"(?:[A-Za-z][A-Za-z0-9\s,.'\"!?;:\-()]{8,})")


@dataclass
class EnglishSpanIssue:
    """検知された英語スパンの中途切断問題。"""

    file_name: str
    unit_id: str
    field: str
    span_text: str
    reason: str


def _is_mid_sentence_cutoff(span: str) -> bool:
    """英語スパンが中途切断されているかを判定する。"""
    stripped = span.strip()
    if not stripped:
        return False
    # 日本語混入は別タスク（カタカナガード）で扱うため、ここでは英語純粋性を前提としない。
    if _TRAILING_HYPHEN_RE.search(stripped):
        return True
    if _TRAILING_FRAGMENT_RE.search(stripped):
        return True
    return False


def _collect_english_spans(content: dict[str, Any]) -> list[tuple[str, str, str]]:
    """(unit_id, field, span_text) のリストを抽出する。

    - document: tts.text 内の [en-US]...[ja-JP] スパン、および text 内の独立英語ブロック
    - quiz: 該当なし（クイズは別タスク）
    """
    results: list[tuple[str, str, str]] = []
    if content.get("type") != "document":
        return results

    for doc in content.get("documents") or []:
        if not isinstance(doc, dict):
            continue
        unit_id = str(doc.get("id") or "")
        tts_text = (doc.get("tts") or {}).get("text") or ""
        for m in _LANGUAGE_SPAN_RE.finditer(tts_text):
            span = m.group(1).strip()
            if span:
                results.append((unit_id, "tts.text", span))
        body_text = doc.get("text") or ""
        for m in _STANDALONE_ENGLISH_RE.finditer(body_text):
            span = m.group(0).strip()
            if span:
                results.append((unit_id, "text", span))
    return results


def validate_english_spans(content: dict[str, Any], file_name: str = "") -> list[EnglishSpanIssue]:
    """コンテンツ内の英語スパンについて中途切断を検知する。

    戻り値: 検知された問題のリスト（空なら問題なし）。
    """
    issues: list[EnglishSpanIssue] = []
    for unit_id, field, span in _collect_english_spans(content):
        if _is_mid_sentence_cutoff(span):
            reason = "英語スパンが中途で切断されています（語脱落またはハイフン中途）。"
            issues.append(
                EnglishSpanIssue(
                    file_name=file_name,
                    unit_id=unit_id,
                    field=field,
                    span_text=span,
                    reason=reason,
                )
            )
    if issues:
        logger.warning(
            "content_validator.english_span_issues file=%s count=%s",
            file_name,
            len(issues),
        )
        for issue in issues:
            logger.warning(
                "content_validator.issue file=%s unit_id=%s field=%s span=%r reason=%s",
                issue.file_name,
                issue.unit_id,
                issue.field,
                issue.span_text[:80],
                issue.reason,
            )
    return issues
