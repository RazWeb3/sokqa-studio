import json
from pathlib import Path

from app.config import get_settings
from app.services import packops


def _configure_local_storage(tmp_path: Path, monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "local_storage_dir", str(tmp_path / "generated"))
    monkeypatch.setattr(settings, "public_base_url", "http://localhost:8000/generated")
    monkeypatch.setattr(settings, "gcs_prefix", "sokqa")
    monkeypatch.setattr(settings, "r2_prefix", "sokqa")


def _document(with_placeholder: bool = False) -> dict:
    text = "スクラムでは〇〇が責任を持つ。" if with_placeholder else "スクラムでは開発チームが責任を持つ。"
    return {
        "id": "doc_pack", "type": "document", "schemaVersion": 1, "title": "Doc",
        "documents": [{"id": "doc-1", "text": text}],
    }


def _quiz() -> dict:
    return {
        "id": "quiz_pack", "type": "quiz", "schemaVersion": 1, "title": "Quiz",
        "questions": [
            {
                "id": "q-1", "question": "スクラムの役割は?", "choices": ["開発チーム", "営業", "経理", "人事"],
                "answerIndex": 0, "explanation": "開発チームです。",
            }
        ],
    }


def _v1_manifest() -> dict:
    return {
        "id": "pack_v1", "type": "pack_manifest", "schemaVersion": 1,
        "title": "テストパック", "description": "説明", "language": "ja",
        "items": [
            {"kind": "document", "url": "https://placeholder.example/doc.json"},
            {"kind": "quiz", "url": "https://placeholder.example/quiz.json"},
        ],
    }


def _make_draft(root: Path, *, placeholder: bool = False) -> Path:
    slug_dir = root / "packs" / "pm_test"
    slug_dir.mkdir(parents=True)
    (slug_dir / "doc_pm_test.json").write_text(
        json.dumps(_document(placeholder), ensure_ascii=False), encoding="utf-8"
    )
    (slug_dir / "quiz_pm_test.json").write_text(
        json.dumps(_quiz(), ensure_ascii=False), encoding="utf-8"
    )
    (slug_dir / "pack_pm_test_manifest.json").write_text(
        json.dumps(_v1_manifest(), ensure_ascii=False), encoding="utf-8"
    )
    return slug_dir


def test_validate_flags_placeholder_as_not_publish_ready(tmp_path, monkeypatch) -> None:
    _configure_local_storage(tmp_path, monkeypatch)
    draft = _make_draft(tmp_path, placeholder=True)

    result = packops.validate_pack_dir(draft)

    assert result["valid"] is True  # 保存ゲートは変更しない（technical error なし）
    assert result["publishReady"] is False
    assert any("〇〇" in hit["message"] for hit in result["placeholderHits"])


def test_validate_passes_when_clean(tmp_path, monkeypatch) -> None:
    _configure_local_storage(tmp_path, monkeypatch)
    draft = _make_draft(tmp_path, placeholder=False)

    result = packops.validate_pack_dir(draft)

    assert result["valid"] is True
    assert result["publishReady"] is True
    assert result["placeholderHits"] == []


def test_import_writes_lock_and_syncs_manifest_urls(tmp_path, monkeypatch) -> None:
    _configure_local_storage(tmp_path, monkeypatch)
    draft = _make_draft(tmp_path, placeholder=False)

    result = packops.import_pack_dir(draft, creator_id="creator_packops")

    assert result["revision"] == 1
    assert result["publishReady"] is True
    lock = json.loads((draft / "packops.lock.json").read_text(encoding="utf-8"))
    assert lock["creatorId"] == "creator_packops"
    assert lock["versionId"] == result["versionId"]
    # v1 manifest の item URL が配置版の実 URL へ差し替わっている
    v1 = json.loads((draft / "pack_pm_test_manifest.json").read_text(encoding="utf-8"))
    assert all("placeholder.example" not in item["url"] for item in v1["items"])
    assert all("cdn" in item["url"] or "generated" in item["url"] for item in v1["items"])


def test_import_refuses_when_r2_diverged_and_allows_force(tmp_path, monkeypatch) -> None:
    _configure_local_storage(tmp_path, monkeypatch)
    draft = _make_draft(tmp_path, placeholder=False)
    packops.import_pack_dir(draft, creator_id="creator_packops")
    # Site 側で再生成され R2 latest が進んだ状況を lock のみで偽装する
    lock_path = draft / "packops.lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["versionId"] = "v00000000_000000"
    lock_path.write_text(json.dumps(lock), encoding="utf-8")

    raised = False
    try:
        packops.import_pack_dir(draft, creator_id="creator_packops")
    except ValueError as exc:
        raised = "diverged" in str(exc)
    assert raised

    forced = packops.import_pack_dir(draft, creator_id="creator_packops", force=True)
    assert forced["revision"] == 2


def test_snapshot_is_deterministic_and_publish_aware(tmp_path, monkeypatch) -> None:
    _configure_local_storage(tmp_path, monkeypatch)
    draft = _make_draft(tmp_path, placeholder=True)
    packops.import_pack_dir(draft, creator_id="creator_packops")
    packs_root = tmp_path / "packs"

    first = packops.snapshot_registry(packs_root=packs_root)
    entry_path = packs_root / "registry" / "creator_packops" / "pm_test.json"
    assert "creator_packops/pm_test" in " ".join(first["written"])
    first_bytes = entry_path.read_bytes()
    entry = json.loads(first_bytes.decode("utf-8"))
    # ローカルドラフトが latest と一致 → プレースホルダー考慮した publishReady=false
    assert entry["publishReady"] is False
    assert entry["publishReadyBasis"] == "local_draft_validation"
    assert entry["draftSynced"] is True

    packops.snapshot_registry(packs_root=packs_root)
    assert entry_path.read_bytes() == first_bytes  # 決定論性
