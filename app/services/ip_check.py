"""層4 表記チェック（IP クリアランスの機械部）。docs/QUALITY_OPS_IMPLEMENTATION_PLAN.md §7 参照。

法的判定は**断定しない**。ここが返すのは「公開前に人が確認すべき表記上の所見」だけで、
違法/合否の結論ではない。各所見は blocking=False とし、既存の機械検証
（validation.valid / qualityStatus / publishReady）とは**合成せず**別枠で合流させる（§0-3）。

チェック対象は学習者向けテキスト（document/quiz の文字列）＋任意で sources.json の宣言:
- 商標の帰属表記: PMBOK®/PMP®/CAPM® 等の PMI 系商標や Scrum 等が本文中に出るのに
  「登録商標 / 商標 / trademark」の帰属一文が見当たらない → escalate
- 非公式の明記: 「試験対策/受験/合格/認定資格」色の表現があるのに「公式ではありません」
  系の免責が見当たらない → notice
- 過去問の示唆: 「過去問」出現 → escalate（過去問の再録は権利処理が別で必要）
- 出典台帳との突合: 検出した商標・規格名が sources.json に未登録 → info（宣言の推奨）
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterable

# 所有団体・帰属表記が通常要求される商標ファミリー。pattern は本文照合用、
# attribution_hints は「どの所有者への帰属があれば足りるかと見なすか」の手がかり。
TRADEMARK_FAMILIES: list[dict[str, Any]] = [
    {
        "id": "pmi",
        "pattern": re.compile(
            r"PMBOK|PMBoK|(?<![\w])PMP(?![\w])|(?<![\w])CAPM(?![\w])|PGMP|PfMP|PMI-ACP|PMI-RP|(?<![\w])PMI(?![\w])"
        ),
        "owner": "Project Management Institute (PMI)",
        "attribution_hints": re.compile(r"PMI|Project Management Institute", re.IGNORECASE),
        "example": "PMP® and PMI are marks of PMI.",
    },
    {
        "id": "scrum",
        "pattern": re.compile(r"Scrum", re.IGNORECASE),
        "owner": "Scrum.org / Scrum Alliance（Scrum Guide は CC BY-SA 4.0）",
        "attribution_hints": re.compile(r"Scrum\.org|Scrum Alliance|Scrum Guide|Ken Schwaber|Jeff Sutherland", re.IGNORECASE),
        "example": "Scrum は Scrum.org の商標です。",
    },
    {
        "id": "prince2",
        "pattern": re.compile(r"PRINCE2", re.IGNORECASE),
        "owner": "AXELOS / PeopleCert",
        "attribution_hints": re.compile(r"AXELOS|PeopleCert", re.IGNORECASE),
        "example": "PRINCE2 は PeopleCert の登録商標です。",
    },
    {
        "id": "itil",
        "pattern": re.compile(r"(?<![\w])ITIL(?![\w])", re.IGNORECASE),
        "owner": "PeopleCert",
        "attribution_hints": re.compile(r"PeopleCert|AXELOS", re.IGNORECASE),
        "example": "ITIL は PeopleCert の商標です。",
    },
    {
        "id": "safe",
        "pattern": re.compile(r"(?<![\w])SAFe(?![\w])"),
        "owner": "Scaled Agile, Inc.",
        "attribution_hints": re.compile(r"Scaled Agile", re.IGNORECASE),
        "example": "SAFe は Scaled Agile, Inc. の登録商標です。",
    },
    {
        "id": "agile_manifesto",
        "pattern": re.compile(r"アジャイルマニフェスト|Agile Manifesto", re.IGNORECASE),
        "owner": "Agile Alliance",
        "attribution_hints": re.compile(r"Agile Alliance|アジャイルアライアンス", re.IGNORECASE),
        "example": "アジャイルマニフェストは Agile Alliance の著作です。",
    },
]

# 帰属・免責の「一文が存在するか」を判定するメタ正規表現（本文と分離して別枠で探す）。
_ATTRIBUTION_CUE = re.compile(r"登録商標|商標|trademark|registered mark|\bmark[s]?\sof\b|©|©️|All rights reserved", re.IGNORECASE)
_EXAM_CUE = re.compile(r"試験対策|受験対策|資格試験|認定資格|模擬試験|CBT|合格")
_PAST_EXAM_CUE = re.compile(r"過去問")
# 「公式ではありません」系の免責（正規の否定表現を広く拾う）。
_DISCLAIMER_CUE = re.compile(
    r"公式[^。\n]{0,12}(?:では)?(?:あり)?ません"
    r"|非公式"
    r"|(?:関係|承認|提携|保証)(?:が|を)?(?:ありません|していない|されていません)"
    r"|official[^.\n]{0,24}not",
    re.IGNORECASE,
)

FindingSeverity = str  # "escalate" | "notice" | "info"


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "")


def _iter_strings(obj: Any) -> Iterable[str]:
    """スキーマ非依存で、dict/list 内のすべての文字列を取り出す（学習者向け text を拾うため）。"""
    if isinstance(obj, dict):
        for value in obj.values():
            yield from _iter_strings(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _iter_strings(value)
    elif isinstance(obj, str):
        yield obj


def _blob_from_files(files: Iterable[Any]) -> str:
    # files: .content を持つオブジェクト（GeneratedFile 等）または生の dict のいずれか。
    parts: list[str] = []
    for file in files:
        content = getattr(file, "content", file)
        parts.extend(_iter_strings(content))
    return _normalize(" \n".join(parts))


def _sources_names(sources: dict[str, Any] | None) -> str:
    if not sources:
        return ""
    names: list[str] = []
    for ref in sources.get("sources", []) or []:
        if isinstance(ref, dict):
            names.extend([str(ref.get("name", "")), str(ref.get("url", "")), str(ref.get("license", ""))])
    return _normalize(" \n".join(names))


def _finding(
    *,
    kind: str,
    severity: FindingSeverity,
    message: str,
    suggestion: str,
    marks: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "severity": severity,
        "marks": marks or [],
        "message": message,
        "suggestion": suggestion,
        "blocking": False,  # 法的断定をしない＝常に非ブロッキング。公開可否は人間判断に委ねる
    }


def check_ip_representation(files: Iterable[Any], sources: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """パック本文（と任意で sources.json）から表記上の IP 所見を列挙する。決定的・LLM 不使用。"""
    blob = _blob_from_files(files)
    sources_blob = _sources_names(sources)
    findings: list[dict[str, Any]] = []

    has_attribution_cue = bool(_ATTRIBUTION_CUE.search(blob))
    detected_owners: list[str] = []

    for family in TRADEMARK_FAMILIES:
        hits = family["pattern"].findall(blob)
        if not hits:
            continue
        distinct_marks = sorted({str(h).upper() if isinstance(h, str) else h for h in hits})
        # 本文に商標が出ていても、所有者への帰属一文があれば「表記上は配慮あり」とする。
        attribution_ok = has_attribution_cue and bool(family["attribution_hints"].search(blob))
        sources_lower = sources_blob.lower()
        declared_in_sources = bool(sources_blob) and (
            bool(family["attribution_hints"].search(sources_blob))
            or any(mark.lower() in sources_lower for mark in distinct_marks)
        )
        detected_owners.append(family["owner"])
        if not attribution_ok:
            findings.append(
                _finding(
                    kind="trademark_attribution",
                    severity="escalate",
                    marks=distinct_marks,
                    message=(
                        f"商標 {distinct_marks}（{family['owner']}）が本文に含まれますが、"
                        "帰属表記（例: 「〜は PMI の登録商標です」）が見当たりません。"
                    ),
                    suggestion=(
                        f"冒頭または末尾に帰属一文を追加してください。例: 「{family['example']}」"
                        + ("" if declared_in_sources else "併せて sources.json に出典を宣言すると追溯可能です。")
                    ),
                )
            )
        elif not declared_in_sources:
            findings.append(
                _finding(
                    kind="source_declared",
                    severity="info",
                    marks=distinct_marks,
                    message=f"商標 {distinct_marks}（{family['owner']}）を出典台帳 sources.json に未登録です。",
                    suggestion="sources.json の sources に該当規格・ライセンスを追記してください（任意・追溯性向上）。",
                )
            )

    if _PAST_EXAM_CUE.search(blob):
        findings.append(
            _finding(
                kind="past_exam_claim",
                severity="escalate",
                marks=["過去問"],
                message="「過去問」を想起させる表現があります。実際の試験問題の再録は別途権利処理が必要です。",
                suggestion="自作の類題であることを明示するか、「過去問」という表記を避けてください。",
            )
        )

    if _EXAM_CUE.search(blob) and not _DISCLAIMER_CUE.search(blob):
        findings.append(
            _finding(
                kind="exam_disclaimer",
                severity="notice",
                marks=sorted({m for m in re.findall(_EXAM_CUE, blob)}),
                message="試験対策・資格をうたう表現がありますが、「公式試験対策ではない」旨の免責が見当たりません。",
                suggestion=(
                    "「本教材は〇〇の公式試験対策ではなく、関連団体による承認・保証を受けていません」"
                    "のような免責一文の追加を推奨します。"
                ),
            )
        )

    return findings


def summarize_ip(findings: list[dict[str, Any]]) -> dict[str, Any]:
    """所見リストから公開判断用の要約（レビュー要否と深刻度カウント）を作る。合否は返さない。"""
    counts = {"escalate": 0, "notice": 0, "info": 0}
    for finding in findings:
        counts[finding["severity"]] = counts.get(finding["severity"], 0) + 1
    return {
        "reviewNeeded": counts["escalate"] > 0 or counts["notice"] > 0,
        "counts": counts,
        "note": "表記上の所見のみ。法的適否の断定は含まない（人間最終判断が必要）",
    }
