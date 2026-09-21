"""チャット教材レビュー用の読み取り専用・オフライン補助検査。

サイトの保存判定は変更しない。所見ゼロは品質・適法性・発音の保証ではない。
意味理解はチャットスキルが担当し、このモジュールは再現可能な検出結果を返す。
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from pydantic import ValidationError

from app.schemas.sokqa import SokqaDocumentPack, SokqaQuizPack
from app.services.ip_check import check_ip_representation
from app.services.packops import load_pack_dir, validate_pack_dir
from app.services.tts_estimation import extract_recording_units
from app.services.tts_language_tags import parse_tts_language_segments
from app.services.tts_sentence_policy import find_long_sentences

# 単なる英語・日本語の併記は誤りではないため、混在は info に限定する。
TERM_VARIANTS = (("インターフェース", "インタフェース"), ("サーバー", "サーバ"), ("Scrum", "スクラム"))
TYPO_PATTERNS = (
    ("repeated_ending", re.compile(r"(?:ですです|ますます[。！？]|であるである|についてについて)")),
    ("duplicate_punctuation", re.compile(r"[。、]{2,}")),
    ("replacement_character", re.compile("\ufffd")),
)
ACRONYM = re.compile(r"(?<![A-Za-z0-9])[A-Z][A-Z0-9]{1,9}(?![A-Za-z0-9])")
SPEECH_SYMBOL = re.compile(r"https?://\S+|[≤≥≠→←⇒]|\d+(?:\.\d+)?\s*(?:%|％|/|〜|～)")
VISUAL_REFERENCE = re.compile(r"上図|下図|左図|右図|赤字|青字|下線部|次の図|次の表")


def _fingerprint(value: Any) -> str:
    """JSON の意味内容を固定順でハッシュ化する（ファイルのバイト列ではない）。"""
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _finding(category: str, rule: str, file: str, path: str, excerpt: str,
             message: str, suggestion: str, *, severity: str = "notice",
             basis: str = "heuristic", **details: Any) -> dict[str, Any]:
    return {
        "category": category, "rule": rule, "severity": severity,
        "location": {"file": file, "path": path}, "excerpt": excerpt[:180],
        "message": message, "suggestion": suggestion, "basis": basis,
        "blocking": False, **details,
    }


def _display_fields(content: dict[str, Any]) -> Iterator[tuple[str, str]]:
    """URL・ID・TTS を校正対象とせず、学習者向け本文だけを取り出す。"""
    for key in ("title", "description"):
        if isinstance(content.get(key), str):
            yield key, content[key]
    key = "documents" if content.get("type") == "document" else "questions"
    items = content.get(key)
    for index, item in enumerate(items if isinstance(items, list) else []):
        if not isinstance(item, dict):
            continue
        for field in (("text",) if key == "documents" else ("question", "explanation")):
            if isinstance(item.get(field), str):
                yield f"{key}.{index}.{field}", item[field]
        choices = item.get("choices")
        for choice_index, choice in enumerate(choices if isinstance(choices, list) else []):
            if isinstance(choice, str):
                yield f"{key}.{index}.choices.{choice_index}", choice


def _text_findings(file: str, fields: list[tuple[str, str]]) -> list[dict[str, Any]]:
    findings = []
    for path, text in fields:
        for rule, pattern in TYPO_PATTERNS:
            for match in pattern.finditer(text):
                findings.append(_finding(
                    "text", rule, file, path, text[max(0, match.start() - 20):match.end() + 30],
                    "誤字・重複表現の候補です。引用や意図した表現か確認してください。",
                    "前後の文脈を確認し、誤りの場合のみ該当部分を修正する。"))
        match = VISUAL_REFERENCE.search(text)
        if match:
            findings.append(_finding(
                "accessibility", "visual_reference", file, path, match.group(),
                "視覚情報への参照があります。音声だけで理解できるか確認が必要です。",
                "参照先の有無と、音声向け説明の必要性を確認する。"))
    for variants in TERM_VARIANTS:
        locations = []
        for term in variants:
            # サーバーの部分文字列をサーバの出現として数えない。
            pattern = re.compile(re.escape(term) + (r"(?!ー)" if term == "サーバ" else ""))
            locations.append([(path, text) for path, text in fields if pattern.search(text)])
        if all(locations):
            findings.append(_finding(
                "consistency", "term_variants", file, locations[0][0][0], " / ".join(variants),
                "表記の併用があります。対訳・初出説明なら問題ありません。",
                "用語方針と文脈を確認し、意図しない揺れだけを統一する。", severity="info",
                relatedLocations=[{"file": file, "path": items[0][0]} for items in locations]))
    return findings


def _quiz_stats(file: str, pack: SokqaQuizPack) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    counts = Counter(question.answerIndex for question in pack.questions)
    total = len(pack.questions)
    stats = {"file": file, "questionCount": total,
             "answerCounts": {str(index): counts[index] for index in range(4)},
             "maxAnswerShare": max(counts.values(), default=0) / total if total else 0}
    findings = []
    if total >= 8 and stats["maxAnswerShare"] > 0.6:
        findings.append(_finding(
            "quiz_quality", "answer_concentration", file, "questions.answerIndex", str(stats["answerCounts"]),
            "8問以上の設問で、同じ位置の正解が60%を超えています。",
            "出題設計を確認する。選択肢を移す場合は正解・解説・TTSの対応も保つ。",
            severity="warning", basis="statistic"))
    for index, question in enumerate(pack.questions):
        normalize = lambda text: re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()
        if normalize(question.explanation) == normalize(question.choices[question.answerIndex]):
            findings.append(_finding(
                "quiz_quality", "answer_only_explanation", file, f"questions.{index}.explanation",
                question.explanation, "解説が正解選択肢の繰り返しだけになっています。",
                "なぜ正解なのか、誤答とどう違うのかの説明が必要か確認する。"))
    return stats, findings


def _speech_targets(pack: SokqaDocumentPack | SokqaQuizPack):
    """既存の録音抽出順に合わせて、JSON位置とフィールド別言語を付与する。"""
    if isinstance(pack, SokqaDocumentPack):
        for index, item in enumerate(pack.documents):
            yield f"documents.{index}.text", f"documents.{index}.tts.text", (item.tts.textLanguage if item.tts else None) or pack.language
    else:
        for index, item in enumerate(pack.questions):
            prefix = f"questions.{index}"
            for field, tts_field, language_field in (("question", "questionText", "questionLanguage"),):
                yield f"{prefix}.{field}", f"{prefix}.tts.{tts_field}", (getattr(item.tts, language_field) if item.tts else None) or pack.language
            for choice in range(len(item.choices)):
                yield f"{prefix}.choices.{choice}", f"{prefix}.tts.choiceTexts.{choice}", (item.tts.choicesLanguage if item.tts else None) or pack.language
            yield f"{prefix}.explanation", f"{prefix}.tts.explanationText", (item.tts.explanationLanguage if item.tts else None) or pack.language


def _tts_findings(file: str, pack: SokqaDocumentPack | SokqaQuizPack, text_source: str):
    findings = []
    units = extract_recording_units(pack, text_source=text_source)
    for unit, (raw_path, corrected_path, language) in zip(units, _speech_targets(pack), strict=True):
        path = corrected_path if unit.used_text_source == "corrected" else raw_path
        details = {"textSource": unit.used_text_source, "recordingUnitId": unit.item_id}
        segments = parse_tts_language_segments(unit.text, language or "ja")
        for segment in segments:
            if segment.language_code.lower().startswith("ja"):
                marks = sorted(set(ACRONYM.findall(segment.text)))
                if marks:
                    findings.append(_finding(
                        "tts_readability", "acronym_reading", file, path, " / ".join(marks),
                        "日本語の読み上げ対象に英字略語があります。誤読は未確認です。",
                        "文脈に合う読みと既存TTS補正を確認し、必要時のみ辞書・補正を検討する。", **details))
            match = SPEECH_SYMBOL.search(segment.text)
            if match:
                findings.append(_finding(
                    "tts_readability", "symbol_reading", file, path, match.group(),
                    "URL・数値表記・記号の読み方を確認する候補です。",
                    "読み上げモデルでの試聴か、意味を維持したTTS補正を検討する。", **details))
        for sentence in find_long_sentences(unit.text):
            findings.append(_finding(
                "tts_readability", "long_sentence", file, path, sentence,
                "既存の長文検出基準に該当します。音声モデルによって読み上げに影響します。",
                "句点による自然な分割や録音設定を確認する。", **details))
    return findings, len(units)


def review_pack_dir(slug_dir: Path, *, text_source: str = "raw", max_findings: int = 200) -> dict[str, Any]:
    """ローカルのパックだけを検査する。ネットワーク・課金・書込は行わない。"""
    if text_source not in {"raw", "corrected"}:
        raise ValueError("text_source must be raw or corrected")
    if max_findings < 1:
        raise ValueError("max_findings must be positive")
    gate = validate_pack_dir(slug_dir)
    draft = load_pack_dir(slug_dir)
    findings = []
    for error in gate["errors"]:
        findings.append(_finding(
            "structure" if error["classification"] == "technical" else "content",
            "existing_validation", error["file"], error["path"], "", error["message"],
            "既存validatorの指摘を原文と照合する。",
            severity="error" if error["classification"] == "technical" else "warning", basis="validator"))
    file_info, quiz_stats, skipped = [], [], []
    display_count = speech_count = 0
    for file in draft.files:
        fields = list(_display_fields(file.content))
        display_count += len(fields)
        file_info.append({"name": file.name, "kind": file.kind, "contentHash": _fingerprint(file.content),
                          "displayFieldCount": len(fields)})
        findings.extend(_text_findings(file.name, fields))
        # 既存IPルールは未検証の所有者名・追記例を含む。法的事実として転記しない。
        for ip in check_ip_representation([file], draft.sources):
            marks = ip["marks"]
            path = next((path for path, text in fields if any(mark.casefold() in text.casefold() for mark in marks)), "$")
            findings.append(_finding(
                "ip", ip["kind"], file.name, path, " / ".join(marks),
                "既存表記ルールの検出候補です。出典・帰属・非公式表記の要否は未確認です。",
                "公式の利用条件と原文を確認する。用語の出現だけで転載や表記義務を断定せず、所有者名を推測して追記しない。",
                severity={"escalate": "warning", "notice": "notice", "info": "info"}[ip["severity"]],
                basis="legacy_ip_heuristic"))
        model = SokqaDocumentPack if file.kind == "document" else SokqaQuizPack
        try:
            pack = model.model_validate(file.content)
        except ValidationError:
            skipped.append({"file": file.name, "checks": ["quiz_statistics", "tts_prediction"],
                            "reason": "スキーマ検証に失敗。既存validationの所見を確認してください。"})
            continue
        items = pack.documents if isinstance(pack, SokqaDocumentPack) else pack.questions
        if not items:
            findings.append(_finding("content", "empty_pack", file.name, "documents" if file.kind == "document" else "questions",
                                     "[]", "学習項目がありません。", "本文または設問の欠落を確認する。", severity="warning", basis="statistic"))
        ids = Counter(item.id for item in items)
        for index, item in enumerate(items):
            if ids[item.id] > 1:
                findings.append(_finding("content", "duplicate_id", file.name,
                                         f"{'documents' if file.kind == 'document' else 'questions'}.{index}.id", item.id,
                                         "同一ファイル内に重複IDがあります。", "参照と音声対応を確認し、一意なIDにする。", severity="warning", basis="statistic"))
        if isinstance(pack, SokqaQuizPack):
            stats, quiz_findings = _quiz_stats(file.name, pack)
            quiz_stats.append(stats)
            findings.extend(quiz_findings)
        tts_findings, count = _tts_findings(file.name, pack, text_source)
        findings.extend(tts_findings)
        speech_count += count
    if not draft.sources or not draft.sources.get("sources"):
        findings.append(_finding("sources", "sources_unrecorded", "sources.json", "sources", "",
                                 "参照元の記録がありません。参照していないという意味ではありません。",
                                 "確認できる入力資料・取得資料のみ記録する。学習由来の出所を推測しない。", severity="info"))
    for index, source in enumerate((draft.sources or {}).get("sources", [])):
        if source.get("usage") == "verbatim" and not source.get("verbatimAllowed"):
            findings.append(_finding("sources", "verbatim_permission_unconfirmed", "sources.json", f"sources.{index}", source["name"],
                                     "逐語利用の申告がありますが、許諾ありの記録がありません。",
                                     "許諾・ライセンス・権利制限規定など実際の利用根拠を確認する。", severity="warning"))
    order = {"error": 0, "warning": 1, "notice": 2, "info": 3}
    findings.sort(key=lambda f: (order[f["severity"]], f["category"], f["location"]["file"], f["location"]["path"], f["rule"]))
    for finding in findings:
        finding["id"] = _fingerprint(finding)[:16]
    counts = Counter(f["severity"] for f in findings)
    return {
        "schemaVersion": 1, "reviewedAt": datetime.now(timezone.utc).isoformat(),
        "pack": slug_dir.name, "mode": "offline_deterministic", "textSource": text_source,
        "files": file_info,
        "sourcesHash": _fingerprint(draft.sources),
        "existingValidation": {key: gate[key] for key in ("valid", "publishReady", "errors", "placeholderHits")},
        "coverage": {"displayFields": display_count, "speechUnits": speech_count, "skipped": skipped,
                     "semanticReview": "not_performed", "legalReview": "not_performed", "audioListening": "not_performed",
                     "remoteLatest": "not_checked", "manifestReferences": "not_checked"},
        "quizStatistics": quiz_stats, "findings": findings[:max_findings],
        "summary": {"total": len(findings), "returned": min(len(findings), max_findings),
                    "counts": {key: counts[key] for key in order}, "truncated": len(findings) > max_findings},
        "limitations": [
            "機械的な候補検出だけです。意味・事実・権利・実音声はチャットで別途確認してください。",
            "publishReadyは既存の技術検証とplaceholder判定であり、法的な公開許可ではありません。",
            "英字・表記揺れ・免責の検出には誤検知と見逃しがあります。補正や辞書は自動適用しません。",
            "このレポートの所見は保存ゲートを変更しません。Gemini/TTS APIもR2も呼びません。",
        ],
    }


def render_review_markdown(report: dict[str, Any]) -> str:
    """同じ構造化結果を人が読む表にする。全文JSONはCLIの既定出力。"""
    def cell(value: Any) -> str:
        return html.escape(str(value)).replace("|", "\\|").replace("\n", " ").replace("\r", " ").replace("`", "\\`")

    summary = report["summary"]
    lines = [f"# 教材レビュー: {cell(report['pack'])}", "",
             f"機械検査のみ / 読み上げ対象: {report['textSource']} / 所見: {summary['total']}件（表示{summary['returned']}件）",
             "意味・権利・実音声の確認は未実施です。公開可否の判定ではありません。", "",
             "| 深刻度 | 分類 | 位置 | 根拠・抜粋 | 所見 | 次の確認 |", "| --- | --- | --- | --- | --- | --- |"]
    for finding in report["findings"]:
        location = finding["location"]
        values = [finding["severity"], finding["category"], f"{location['file']}:{location['path']}",
                  finding["excerpt"] or finding["basis"], finding["message"], finding["suggestion"]]
        lines.append("| " + " | ".join(cell(value) for value in values) + " |")
    if summary["truncated"]:
        lines.append("\n所見を省略しています。--max-findings を増やして全件を確認してください。")
    if report["coverage"]["skipped"]:
        lines.append("\nスキーマ不適合で一部検査をスキップしました。JSONのcoverage.skippedを確認してください。")
    lines.extend(["", *[f"- {text}" for text in report["limitations"]]])
    return "\n".join(lines) + "\n"
