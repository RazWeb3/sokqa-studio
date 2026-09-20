import json

from app.services.ip_check import check_ip_representation, summarize_ip


def _doc(text: str) -> dict:
    return {
        "id": "doc_pack", "type": "document", "schemaVersion": 1, "title": "Doc",
        "documents": [{"id": "d1", "text": text}],
    }


def _marks(findings: list[dict]) -> set[tuple[str, str]]:
    return {(f["kind"], f["severity"]) for f in findings}


def test_trademark_without_attribution_escalates() -> None:
    findings = check_ip_representation([_doc("PMBOK と PMP に基づく進行管理")])
    assert ("trademark_attribution", "escalate") in _marks(findings)
    pmi = next(f for f in findings if f["kind"] == "trademark_attribution")
    assert "PMBOK" in pmi["marks"] and "PMP" in pmi["marks"]
    assert pmi["blocking"] is False  # 法的断定をしない＝常に非ブロッキング


def test_trademark_with_attribution_drops_escalate_and_adds_info() -> None:
    text = "PMBOK は PMI の登録商標です。PMP も同様の商標です。"
    findings = check_ip_representation([_doc(text)])
    # 帰属一文があるので escalate にはしない。ただし sources 未登録なら info 止まり
    assert ("trademark_attribution", "escalate") not in _marks(findings)
    assert ("source_declared", "info") in _marks(findings)


def test_attribution_plus_source_declaration_is_clean_for_pmi() -> None:
    text = "PMBOK は PMI の登録商標です。"
    sources = {"sources": [{"name": "PMBOK Guide", "license": "proprietary"}]}
    findings = check_ip_representation([_doc(text)], sources)
    assert not any(f["kind"] in {"trademark_attribution", "source_declared"} for f in findings)


def test_exam_prep_without_disclaimer_is_notice() -> None:
    findings = check_ip_representation([_doc("PMP 試験対策の要点整理")])
    assert ("exam_disclaimer", "notice") in _marks(findings)


def test_exam_prep_with_disclaimer_is_satisfied() -> None:
    text = "PMP 試験対策の要点。本教材は公式試験対策ではなく、PMI には承認されていません。"
    findings = check_ip_representation([_doc(text)])
    assert ("exam_disclaimer", "notice") not in _marks(findings)


def test_past_exam_claim_escalates() -> None:
    findings = check_ip_representation([_doc("よく出る論点を過去問から分析しよう")])
    assert ("past_exam_claim", "escalate") in _marks(findings)


def test_plain_content_has_no_findings() -> None:
    findings = check_ip_representation([_doc("タスク板を使って進捗を可視化します。")])
    assert findings == []
    assert summarize_ip(findings)["reviewNeeded"] is False


def test_summarize_counts_and_review_flag() -> None:
    findings = check_ip_representation([_doc("PMBOK に基づく PMP 試験対策")])
    summary = summarize_ip(findings)
    assert summary["reviewNeeded"] is True
    assert summary["counts"]["escalate"] >= 1
    assert "断定" in summary["note"]


def test_packops_validate_surfaces_ip_findings_without_gating(tmp_path, monkeypatch) -> None:
    # packops 統合: 商標あり・帰属なしのドラフトで ipFindings が出、publishReady は placeholder のみで決まる
    from app.config import get_settings
    from app.services import packops

    settings = get_settings()
    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "local_storage_dir", str(tmp_path / "generated"))
    monkeypatch.setattr(settings, "public_base_url", "http://localhost:8000/generated")
    monkeypatch.setattr(settings, "gcs_prefix", "sokqa")
    monkeypatch.setattr(settings, "r2_prefix", "sokqa")

    slug_dir = tmp_path / "packs" / "ip_pack"
    slug_dir.mkdir(parents=True)
    (slug_dir / "doc_ip_pack.json").write_text(
        json.dumps(_doc("PMBOK と PMP に基づくプロジェクト管理"), ensure_ascii=False), encoding="utf-8"
    )

    result = packops.validate_pack_dir(slug_dir)

    assert result["ipReviewNeeded"] is True
    assert any(f["kind"] == "trademark_attribution" for f in result["ipFindings"])
    # 商標所見は publishReady を縛らない（placeholder も無いので valid なら true のはず）
    assert result["valid"] is True
    assert result["publishReady"] is True
