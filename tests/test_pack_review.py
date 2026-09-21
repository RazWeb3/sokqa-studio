import json
import socket
from pathlib import Path

import pytest

from app.services import packops
from app.services.gemini_client import GeminiClient
from app.services.pack_review import render_review_markdown, review_pack_dir
from app.services.storage_client import StorageClient
from scripts.packops import main


def _document(text="学習の目的を確認します。", **extra):
    return {"id": "doc", "type": "document", "schemaVersion": 1, "title": "入門",
            "documents": [{"id": "d1", "text": text}], **extra}


def _quiz(count=1):
    return {"id": "quiz", "type": "quiz", "title": "確認問題", "questions": [
        {"id": f"q{index}", "question": f"計画の手順{index + 1}で確認するものはどれですか。",
         "choices": ["目的", "色", "天気", "気分"], "answerIndex": index % 4,
         "explanation": f"手順{index + 1}に応じて確認します。"} for index in range(count)]}


def _draft(tmp_path, *contents, sources=None):
    directory = tmp_path / "draft"
    directory.mkdir()
    for index, content in enumerate(contents):
        (directory / f"{index}.json").write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
    if sources is not None:
        (directory / "sources.json").write_text(json.dumps(sources, ensure_ascii=False), encoding="utf-8")
    return directory


def _rules(report, rule):
    return [finding for finding in report["findings"] if finding["rule"] == rule]


def test_review_is_readonly_offline_and_preserves_existing_gate(tmp_path, monkeypatch):
    directory = _draft(tmp_path, _document("PMBOK の〇〇を確認します。"), _quiz())
    before = {path.name: path.read_bytes() for path in directory.iterdir()}

    def forbidden(*args, **kwargs):
        raise AssertionError("外部API・ストレージ接続は禁止")

    monkeypatch.setattr(GeminiClient, "generate_json", forbidden)
    monkeypatch.setattr(StorageClient, "__init__", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    baseline = packops.validate_pack_dir(directory)
    report = review_pack_dir(directory)
    assert report["existingValidation"] == {key: baseline[key] for key in report["existingValidation"]}
    assert report["existingValidation"]["valid"] is True
    assert report["existingValidation"]["publishReady"] is False
    assert all(finding["blocking"] is False for finding in report["findings"])
    assert report["coverage"]["semanticReview"] == "not_performed"
    assert report["coverage"]["legalReview"] == "not_performed"
    assert report["coverage"]["audioListening"] == "not_performed"
    assert report["coverage"]["remoteLatest"] == "not_checked"
    assert before == {path.name: path.read_bytes() for path in directory.iterdir()}
    assert not (tmp_path / "generated").exists()


def test_report_is_deterministic_except_timestamp_and_hash_tracks_content(tmp_path):
    directory = _draft(tmp_path, _document())
    first, second = review_pack_dir(directory), review_pack_dir(directory)
    first.pop("reviewedAt")
    second.pop("reviewedAt")
    assert first == second
    content = _document("変更した本文です。")
    (directory / "0.json").write_text(json.dumps(content), encoding="utf-8")
    assert first["files"][0]["contentHash"] != review_pack_dir(directory)["files"][0]["contentHash"]


def test_typos_terms_and_visual_references_exclude_metadata_and_tts(tmp_path):
    content = _document("これは入門ですです。下図のサーバーとサーバを比較します。")
    content["documents"][0]["tts"] = {"text": "これは読みですです。", "audioUrl": "https://example.com/ますます。"}
    content["id"] = "ですです"
    directory = _draft(tmp_path, content)
    report = review_pack_dir(directory)
    assert len(_rules(report, "repeated_ending")) == 1
    assert _rules(report, "repeated_ending")[0]["location"]["path"] == "documents.0.text"
    assert _rules(report, "visual_reference")
    assert _rules(report, "term_variants")[0]["severity"] == "info"


def test_single_term_is_not_a_variant_pair(tmp_path):
    directory = _draft(tmp_path, _document("サーバーを再起動します。"))
    assert not _rules(review_pack_dir(directory), "term_variants")


def test_quiz_statistics_and_answer_only_explanation(tmp_path):
    content = _quiz(10)
    for question in content["questions"]:
        question["answerIndex"] = 0
    content["questions"][0]["explanation"] = "目的"
    report = review_pack_dir(_draft(tmp_path, content))
    assert report["quizStatistics"][0]["answerCounts"] == {"0": 10, "1": 0, "2": 0, "3": 0}
    assert _rules(report, "answer_concentration")
    assert _rules(report, "answer_only_explanation")[0]["location"]["path"] == "questions.0.explanation"


def test_small_quiz_does_not_trigger_concentration_heuristic(tmp_path):
    assert not _rules(review_pack_dir(_draft(tmp_path, _quiz(1))), "answer_concentration")


def test_tts_uses_actual_selection_with_sparse_choice_fallback(tmp_path):
    content = _quiz()
    question = content["questions"][0]
    question.update({"question": "SQLを使いますか。", "choices": ["SQL", "API", "色", "気分"],
                     "tts": {"questionText": "エスキューエルを使いますか。", "choiceTexts": ["エスキューエル", "", "", ""]}})
    directory = _draft(tmp_path, content)
    raw = review_pack_dir(directory)
    corrected = review_pack_dir(directory, text_source="corrected")
    raw_paths = {item["location"]["path"] for item in _rules(raw, "acronym_reading")}
    corrected_paths = {item["location"]["path"] for item in _rules(corrected, "acronym_reading")}
    assert raw_paths == {"questions.0.question", "questions.0.choices.0", "questions.0.choices.1"}
    assert corrected_paths == {"questions.0.choices.1"}
    assert _rules(corrected, "acronym_reading")[0]["textSource"] == "raw"
    assert corrected["coverage"]["speechUnits"] == 6


@pytest.mark.parametrize("language,tts", [
    ("en", None),
    ("ja", {"text": "SQL", "textLanguage": "en-US"}),
    ("ja", {"text": "[en-US]SQL[ja-JP]を学びます。"}),
])
def test_foreign_language_spans_are_not_japanese_acronym_errors(tmp_path, language, tts):
    content = _document("SQL", language=language)
    if tts:
        content["documents"][0]["tts"] = tts
    report = review_pack_dir(_draft(tmp_path, content), text_source="corrected")
    assert not _rules(report, "acronym_reading")


def test_symbol_and_long_sentence_candidates(tmp_path):
    content = _document("利用率は50%です。" + "長い文章が続きます" * 80 + "。")
    report = review_pack_dir(_draft(tmp_path, content))
    assert _rules(report, "symbol_reading")
    assert _rules(report, "long_sentence")


def test_ip_reports_are_candidates_without_unverified_owner_claims(tmp_path):
    report = review_pack_dir(_draft(tmp_path, _document("Scrum Guide とアジャイルマニフェストを説明します。")))
    ip_findings = [finding for finding in report["findings"] if finding["category"] == "ip"]
    assert ip_findings
    assert all(finding["basis"] == "legacy_ip_heuristic" for finding in ip_findings)
    assert all("未確認" in finding["message"] for finding in ip_findings)
    assert "Scrum.org の商標" not in json.dumps(report, ensure_ascii=False)
    assert report["existingValidation"]["publishReady"] is True


def test_sources_missing_and_verbatim_declarations_are_not_legal_verdicts(tmp_path):
    sources = {"sources": [{"name": "社内資料", "usage": "verbatim", "verbatimAllowed": False}]}
    directory = _draft(tmp_path, _document(), sources=sources)
    report = review_pack_dir(directory)
    assert not _rules(report, "sources_unrecorded")
    assert _rules(report, "verbatim_permission_unconfirmed")
    sources["sources"][0]["verbatimAllowed"] = True
    (directory / "sources.json").write_text(json.dumps(sources), encoding="utf-8")
    assert not _rules(review_pack_dir(directory), "verbatim_permission_unconfirmed")


def test_invalid_shape_reports_technical_error_and_skipped_analyses(tmp_path):
    content = _document()
    content["documents"] = "不正な型"
    report = review_pack_dir(_draft(tmp_path, content))
    assert report["existingValidation"]["valid"] is False
    assert report["coverage"]["skipped"]
    assert report["summary"]["counts"]["error"] > 0


def test_invalid_quality_schema_is_not_reclassified_as_technical(tmp_path):
    content = _quiz()
    content["questions"][0]["answerIndex"] = 9
    report = review_pack_dir(_draft(tmp_path, content))
    assert report["existingValidation"]["valid"] is True
    assert report["coverage"]["skipped"]
    assert report["summary"]["counts"]["warning"] > 0


def test_duplicate_ids_and_empty_packs(tmp_path):
    first, second = _document(), _document()
    first["documents"].append({"id": "d1", "text": "異なる本文です。"})
    second["documents"] = []
    report = review_pack_dir(_draft(tmp_path, first, second))
    assert len(_rules(report, "duplicate_id")) == 2
    assert _rules(report, "empty_pack")


def test_truncation_keeps_full_counts_and_prioritizes_errors(tmp_path):
    content = _document("入門ですです。SQLを学びます。")
    invalid = _document()
    invalid.pop("title")
    directory = _draft(tmp_path, content, invalid)
    full = review_pack_dir(directory)
    limited = review_pack_dir(directory, max_findings=1)
    assert limited["summary"]["truncated"] is True
    assert limited["summary"]["counts"] == full["summary"]["counts"]
    assert len(limited["findings"]) == 1
    assert limited["findings"][0]["severity"] == "error"
    assert "所見を省略" in render_review_markdown(limited)


def test_markdown_escapes_untrusted_content(tmp_path):
    report = review_pack_dir(_draft(tmp_path, _document("入門ですです。<script>|悪意</script>")))
    markdown = render_review_markdown(report)
    assert "<script>" not in markdown
    assert "&lt;script&gt;" in markdown
    assert "\\|" in markdown


@pytest.mark.parametrize("bad", ["{", "[]", '{"type":"unknown"}'])
def test_cli_malformed_json_returns_actionable_error(tmp_path, capsys, bad):
    directory = tmp_path / "bad"
    directory.mkdir()
    (directory / "bad.json").write_text(bad, encoding="utf-8")
    assert main(["review", str(directory)]) == 2
    output = capsys.readouterr()
    assert "error" in json.loads(output.err)
    assert output.out == ""


def test_cli_json_markdown_and_invalid_options(tmp_path, capsys):
    directory = _draft(tmp_path, _document())
    assert main(["review", str(directory)]) == 0
    assert json.loads(capsys.readouterr().out)["schemaVersion"] == 1
    assert main(["review", str(directory), "--format", "markdown"]) == 0
    assert "教材レビュー" in capsys.readouterr().out
    assert main(["review", str(directory), "--max-findings", "0"]) == 2
    assert "positive" in capsys.readouterr().err


def test_skill_metadata_and_referenced_files_exist():
    root = Path(__file__).resolve().parents[1]
    text = (root / ".qoder/skills/pack-review/SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\nname: pack-review\ndescription: ")
    assert len(text.splitlines()) < 500
    for path in ("scripts/packops.py", "docs/OPERATOR_RISK_TERMS_GUIDELINES.md"):
        assert path in text
        assert (root / path).is_file()
    assert "Gemini/TTS API、サイトの品質チェックAPIは呼ばない" in text
