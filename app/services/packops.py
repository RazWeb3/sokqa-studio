"""ヘッドレス運用（validate / pull / import / quality-check）のサービス層。

docs/QUALITY_OPS_IMPLEMENTATION_PLAN.md §2/§3 の実装。設計上不変の前提:
- R2（storage）が現状の正典、packs/<slug>/ はドラフト源（入力）。data flow は
  draft→import→R2 と、import 直前の draft←pull←R2 のみ。
- サイト UI と同一の service 関数（import_pack_files / validate_files / quality_checker）
  を呼ぶ単一口径とし、検証を素通りする裏口を作らない。機械検証は LLM を呼ばない。
- packops.lock.json が「ローカルドラフトが対応する配置版」の記録。乖離検出の根拠。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.schemas.pack_sources import PackSourcesFile
from app.schemas.pack_v2 import PackLatestV2, PackManifestV2
from app.schemas.request import (
    ImportPackInputFile,
    ImportPackRequest,
    ImportPackResponse,
    TtsRecordingTarget,
)
from app.schemas.sokqa import GeneratedFile
from app.services.ip_check import check_ip_representation, summarize_ip
from app.services.pack_importer import import_pack_files
from app.services.pack_paths import (
    doc_object_relative_path,
    pack_root_prefix,
    quiz_object_relative_path,
)
from app.services.storage_client import StorageClient

LOCK_FILE_NAME = "packops.lock.json"
SOURCES_FILE_NAME = "sources.json"


def _now_iso() -> str:
    return datetime.now(timezone(timedelta(hours=9))).replace(microsecond=0).isoformat(timespec="seconds")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


@dataclass
class PackDraft:
    """packs/<slug>/ ディレクトリを読み取ったドラフト源。"""

    slug_dir: Path
    files: list[GeneratedFile] = field(default_factory=list)  # document/quiz の順、ファイル名保持
    manifest_v1: dict[str, Any] | None = None
    manifest_v1_path: Path | None = None
    sources: dict[str, Any] | None = None
    lock: dict[str, Any] | None = None

    @property
    def creator_id(self) -> str | None:
        return (self.lock or {}).get("creatorId")

    @property
    def content_id(self) -> str | None:
        return (self.lock or {}).get("contentId")


def load_pack_dir(slug_dir: Path) -> PackDraft:
    if not slug_dir.is_dir():
        raise ValueError(f"pack directory not found: {slug_dir}")
    draft = PackDraft(slug_dir=slug_dir)
    for path in sorted(slug_dir.glob("*.json")):
        if path.name in {LOCK_FILE_NAME, SOURCES_FILE_NAME} or path.name.startswith("."):
            continue
        content = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(content, dict):
            raise ValueError(f"{path.name}: top-level JSON must be an object")
        content_type = content.get("type")
        if content_type == "pack_manifest":
            draft.manifest_v1 = content
            draft.manifest_v1_path = path
        elif content_type == "document":
            draft.files.append(GeneratedFile(name=path.name, kind="document", content=content))
        elif content_type == "quiz":
            draft.files.append(GeneratedFile(name=path.name, kind="quiz", content=content))
        else:
            raise ValueError(f"unsupported JSON type in {path.name}: {content_type or 'missing'}")
    lock_path = slug_dir / LOCK_FILE_NAME
    if lock_path.is_file():
        draft.lock = json.loads(lock_path.read_text(encoding="utf-8"))
    sources_path = slug_dir / SOURCES_FILE_NAME
    if sources_path.is_file():
        PackSourcesFile.model_validate(json.loads(sources_path.read_text(encoding="utf-8")))
        draft.sources = json.loads(sources_path.read_text(encoding="utf-8"))
    return draft


def validate_pack_dir(slug_dir: Path) -> dict[str, Any]:
    """機械検証のみ（LLM 非使用）。公開ゲート用の publishReady を併記する。"""
    draft = load_pack_dir(slug_dir)
    if not draft.files:
        raise ValueError("pack directory has no document or quiz JSON")
    validation = validate_files_for_draft(draft)
    errors = [error.model_dump(mode="json") for error in validation.errors]
    placeholder_hits = [e for e in errors if "placeholder" in str(e.get("message", ""))]
    # 層4 表記チェック（法的断定しない所見）。§0-3 準守で validation.valid/qualityStatus/publishReady
    # とは合成せず別枠で返す。escalate/notice があっても publishReady は動かさない。
    ip_findings = check_ip_representation(draft.files, draft.sources)
    ip_summary = summarize_ip(ip_findings)
    notes: list[str] = []
    if draft.sources is None:
        notes.append(f"{SOURCES_FILE_NAME} がない（出典台帳の同梱を推奨: plans §3）")
    if ip_summary["reviewNeeded"]:
        notes.append("表記チェックで公開前の人間レビュー所見あり（法的適否は本書では断定しない）")
    return {
        "valid": validation.valid,
        "publishReady": validation.valid and not placeholder_hits,
        "errors": errors,
        "placeholderHits": placeholder_hits,
        "ipFindings": ip_findings,
        "ipReviewNeeded": ip_summary["reviewNeeded"],
        "notes": notes,
        "files": [file.name for file in draft.files],
    }


def validate_files_for_draft(draft: PackDraft):
    from app.services.validator import validate_files  # 循環import回避のため遅延

    return validate_files(draft.files)


def _enrich_v1_manifest(draft: PackDraft, creator_id: str, content_id: str) -> dict[str, Any] | None:
    """v1 manifest（Import JSON Profile v1）を PackManifestV2 互換へ補完する。

    値（versionId 等）は import_pack_files 側で再生成されるため仮でよい。意図は
    title/description/language/author/globalTags の引き継ぎ（tmpスクリプト由来の方式）。
    """
    if draft.manifest_v1 is None:
        return None
    return {
        **draft.manifest_v1,
        "contentId": content_id,
        "creator": {"id": creator_id, "displayName": (draft.lock or {}).get("creatorDisplayName")},
        "revision": 1,
        "versionId": "v00000000_000000",
        "buildId": "build_00000000_000000",
        "generatedAt": _now_iso(),
        "change": {"operation": "import"},
        "items": [],
    }


def _item_order(draft: PackDraft) -> list[str]:
    stem = lambda name: name[:-5] if name.lower().endswith(".json") else name  # noqa: E731
    return [stem(file.name) for file in sorted(draft.files, key=lambda f: (f.kind != "document", f.name))]


def import_pack_dir(
    slug_dir: Path,
    *,
    creator_id: str | None = None,
    content_id: str | None = None,
    force: bool = False,
    storage: StorageClient | None = None,
) -> dict[str, Any]:
    """ドラフト源を R2 へ配置し、packops.lock.json と v1 manifest URL を更新する。"""
    draft = load_pack_dir(slug_dir)
    if not draft.files:
        raise ValueError("pack directory has no document or quiz JSON")
    storage = storage or StorageClient()
    creator = creator_id or draft.creator_id
    content = content_id or draft.content_id or slug_dir.name
    if not creator:
        raise ValueError("creatorId is required (no packops.lock.json to infer from)")
    prefix = pack_root_prefix(creator, content)
    latest_data = storage.read_latest(prefix)

    # 保存ゲートは変更しない（plans §3）。プレースホルダーは配置を止めないが、
    # publishReady（公開可否）として結果に載せ、snapshot/レポートで「公開不可」に使う。
    gate = validate_pack_dir(slug_dir)

    if draft.lock and latest_data and not force:
        lock_version = draft.lock.get("versionId")
        latest_version = latest_data.get("versionId")
        if lock_version and latest_version and lock_version != latest_version:
            raise ValueError(
                f"R2 has diverged from the local draft (lock {lock_version} != latest {latest_version}). "
                "Run pull first, or pass force to overwrite deliberately."
            )

    files = [
        ImportPackInputFile(name=file.name, content=file.content) for file in draft.files
    ]
    enriched = _enrich_v1_manifest(draft, creator, content)
    if enriched is not None:
        files.insert(0, ImportPackInputFile(name=draft.manifest_v1_path.name, content=enriched))
    request = ImportPackRequest(
        files=files,
        destination="existing" if latest_data else "new",
        targetContentId=content if latest_data else None,
        conflictStrategy="replace",
        creatorId=creator,
        creatorDisplayName=(draft.lock or {}).get("creatorDisplayName"),
        contentId=content,
        slug=slug_dir.name,
        title=(draft.manifest_v1 or {}).get("title") or draft.files[0].content.get("title"),
        itemOrder=_item_order(draft),
    )
    response = import_pack_files(request)
    _record_lock(slug_dir, creator, content, response)
    _sync_v1_manifest_urls(slug_dir, response)
    return {
        "contentId": content,
        "creatorId": creator,
        "versionId": response.manifest.versionId,
        "revision": response.manifest.revision,
        "qualityStatus": response.manifest.qualityStatus,
        "publishReady": gate["publishReady"],
        "placeholderHits": gate["placeholderHits"],
        "ipFindings": gate["ipFindings"],
        "ipReviewNeeded": gate["ipReviewNeeded"],
        "validation": response.validation.model_dump(mode="json"),
        "logs": response.logs,
    }


def _record_lock(slug_dir: Path, creator: str, content: str, response: ImportPackResponse) -> None:
    manifest_url = next((file.url for file in response.files if file.kind == "manifest"), None)
    _write_json(
        slug_dir / LOCK_FILE_NAME,
        {
            "schemaVersion": 1,
            "creatorId": creator,
            "contentId": content,
            "versionId": response.manifest.versionId,
            "revision": response.manifest.revision,
            "importedAt": _now_iso(),
            "manifestUrl": manifest_url,
            "note": "直近の R2 配置状態のスナップショット。packops の pull/乖離チェックが読み書きする",
        },
    )


def _sync_v1_manifest_urls(slug_dir: Path, response: ImportPackResponse) -> None:
    """v1 manifest の item URL を配置版の実 URL へ差し替える（plans §2 の『要再更新』を自動化）。"""
    manifest_path = next(
        (
            path
            for path in sorted(slug_dir.glob("*.json"))
            if path.name not in {LOCK_FILE_NAME, SOURCES_FILE_NAME}
            and (json.loads(path.read_text(encoding="utf-8")) or {}).get("type") == "pack_manifest"
        ),
        None,
    )
    if manifest_path is None:
        return
    v1 = json.loads(manifest_path.read_text(encoding="utf-8"))
    # v2 items はドラフトの itemOrder と同順で並ぶとは限らないため kind 単位で対応付ける。
    by_kind: dict[str, list[str]] = {}
    for item in response.manifest.items:
        by_kind.setdefault(item.kind, []).append(item.url)
    for entry in v1.get("items", []):
        queue = by_kind.get(str(entry.get("kind")))
        if queue:
            entry["url"] = queue.pop(0)
    _write_json(manifest_path, v1)


def pull_pack_dir(slug_dir: Path, *, storage: StorageClient | None = None) -> dict[str, Any]:
    """R2 latest のオブジェクトをドラフト源へ還流し、lock と v1 manifest URL を同期する。"""
    draft = load_pack_dir(slug_dir)
    if not draft.lock:
        raise ValueError(f"{LOCK_FILE_NAME} not found; run import first to establish the baseline")
    storage = storage or StorageClient()
    creator, content = draft.creator_id, draft.content_id
    prefix = pack_root_prefix(creator, content)
    latest_data = storage.read_latest(prefix)
    if latest_data is None:
        raise ValueError(f"destination manifest was not found in storage: {prefix}")
    latest = PackLatestV2.model_validate(latest_data)
    manifest = PackManifestV2.model_validate(storage.read_manifest(prefix, latest.versionId))
    pulled: list[str] = []
    for item in manifest.items:
        relative_path = (
            doc_object_relative_path(item.fileVersionId)
            if item.kind == "document"
            else quiz_object_relative_path(item.fileVersionId)
        )
        payload = storage.read_object(prefix, relative_path)
        (slug_dir / item.name).write_bytes(payload)
        pulled.append(item.name)
    lock = dict(draft.lock)
    lock.update(
        {
            "versionId": manifest.versionId,
            "revision": manifest.revision,
            "manifestUrl": latest.manifestUrl,
            "pulledAt": _now_iso(),
        }
    )
    _write_json(slug_dir / LOCK_FILE_NAME, lock)
    _sync_manifest_urls_from_v1(slug_dir, manifest)
    return {
        "contentId": content,
        "creatorId": creator,
        "versionId": manifest.versionId,
        "revision": manifest.revision,
        "files": pulled,
    }


def _sync_manifest_urls_from_v1(slug_dir: Path, manifest: PackManifestV2) -> None:
    """v1 manifest ドラフトの item URL を pull した v2 の実URLへ還流する。"""
    for path in sorted(slug_dir.glob("*.json")):
        if path.name in {LOCK_FILE_NAME, SOURCES_FILE_NAME}:
            continue
        content = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(content, dict) or content.get("type") != "pack_manifest":
            continue
        by_kind: dict[str, list[str]] = {}
        for item in manifest.items:
            by_kind.setdefault(item.kind, []).append(item.url)
        for entry in content.get("items", []):
            queue = by_kind.get(str(entry.get("kind")))
            if queue:
                entry["url"] = queue.pop(0)
        _write_json(path, content)
        return


def quality_check_pack_dir(
    slug_dir: Path,
    *,
    mode: str,
    pack_name: str,
    max_issues: int = 50,
) -> dict[str, Any]:
    """保存済みパックへの Gemini 品質チェックを明示実行で呼ぶ（消費に注意）。"""
    from app.services.quality_checker import check_text_quality, check_tts_quality

    draft = load_pack_dir(slug_dir)
    if not draft.lock:
        raise ValueError(f"{LOCK_FILE_NAME} not found; run import first")
    content = _read_local_json(slug_dir / pack_name)
    kind = content.get("type")
    if kind not in {"document", "quiz"}:
        raise ValueError(f"{pack_name}: type must be document or quiz (got {kind!r})")
    target = TtsRecordingTarget(
        creatorId=draft.creator_id,
        contentId=draft.content_id,
        versionId=draft.lock.get("versionId"),
        packName=pack_name,
        kind=kind,
    )
    response = check_text_quality(target, max_issues) if mode == "text" else check_tts_quality(target, max_issues)
    return response.model_dump(mode="json", exclude_none=True)


def _read_local_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
