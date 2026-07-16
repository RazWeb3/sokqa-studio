import json

from fastapi import APIRouter, HTTPException, Response

from app.schemas.request import DeletePackRequest, DeletePackResponse, EditPackManifestRequest, EditPackManifestResponse, ImportPackRequest, ImportPackResponse, RevisePackTtsRequest, TtsRecordingTarget
from app.schemas.sokqa import PackRevisionResponse
from app.services.pack_deletion import delete_pack_version
from app.services.pack_importer import import_pack_files
from app.services.pack_manifest_editor import edit_pack_manifest
from app.services.pack_paths import doc_object_relative_path, pack_root_prefix, quiz_object_relative_path
from app.services.storage_client import StorageClient
from app.schemas.pack_v2 import PackManifestV2
from app.services.pack_listing import list_generated_packs
from app.services.pack_revision_tools import export_pack_json_zip, revise_pack_tts


router = APIRouter(tags=["packs"])


@router.get("/packs")
def list_packs(response: Response, creatorId: str | None = None) -> dict:
    try:
        response.headers["Cache-Control"] = "no-store"
        return {"items": list_generated_packs(creatorId)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/packs/file")
def get_pack_file(creatorId: str, contentId: str, versionId: str, logicalId: str) -> dict:
    try:
        storage = StorageClient()
        manifest = PackManifestV2.model_validate(storage.read_manifest(pack_root_prefix(creatorId, contentId), versionId))
        item = next((candidate for candidate in manifest.items if candidate.logicalId == logicalId), None)
        if item is None:
            raise FileNotFoundError("pack file not found")
        relative_path = doc_object_relative_path(item.fileVersionId) if item.kind == "document" else quiz_object_relative_path(item.fileVersionId)
        return json.loads(storage.read_object(pack_root_prefix(creatorId, contentId), relative_path).decode("utf-8"))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/packs/import", response_model=ImportPackResponse)
def import_packs(request: ImportPackRequest) -> ImportPackResponse:
    try:
        return import_pack_files(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/packs/edit-manifest", response_model=EditPackManifestResponse)
def edit_manifest(request: EditPackManifestRequest) -> EditPackManifestResponse:
    try:
        return edit_pack_manifest(request)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/packs/delete", response_model=DeletePackResponse)
def delete_pack(request: DeletePackRequest) -> DeletePackResponse:
    try:
        return delete_pack_version(request)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/packs/revise-tts", response_model=PackRevisionResponse)
def revise_pack_tts_rules(request: RevisePackTtsRequest) -> PackRevisionResponse:
    try:
        return revise_pack_tts(request)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/packs/export-json-zip")
def export_pack_json_zip_route(target: TtsRecordingTarget) -> Response:
    try:
        payload = export_pack_json_zip(target)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(
        content=payload,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="sokqa-pack-json.zip"'},
    )
