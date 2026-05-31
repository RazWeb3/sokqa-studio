from urllib.parse import urlparse

from pydantic import ValidationError

from app.config import get_settings
from app.schemas.sokqa import (
    GeneratedFile,
    PackManifest,
    SokqaDocumentPack,
    SokqaQuizPack,
    ValidationErrorItem,
    ValidationResult,
)


def _append_pydantic_errors(file_name: str, error: ValidationError, errors: list[ValidationErrorItem]) -> None:
    for issue in error.errors():
        path = ".".join(str(part) for part in issue.get("loc", [])) or "$"
        errors.append(ValidationErrorItem(file=file_name, path=path, message=issue.get("msg", "invalid value")))


def validate_files(files: list[GeneratedFile], manifest: PackManifest | None = None) -> ValidationResult:
    errors: list[ValidationErrorItem] = []
    for file in files:
        try:
            if file.kind == "document":
                SokqaDocumentPack.model_validate(file.content)
            elif file.kind == "quiz":
                SokqaQuizPack.model_validate(file.content)
            elif file.kind == "manifest":
                PackManifest.model_validate(file.content)
        except ValidationError as exc:
            _append_pydantic_errors(file.name, exc, errors)

    if manifest:
        errors.extend(validate_manifest(manifest).errors)

    return ValidationResult(valid=not errors, errors=errors)


def validate_manifest(manifest: PackManifest) -> ValidationResult:
    settings = get_settings()
    errors: list[ValidationErrorItem] = []
    try:
        PackManifest.model_validate(manifest.model_dump())
    except ValidationError as exc:
        _append_pydantic_errors("pack_manifest.json", exc, errors)

    for index, item in enumerate(manifest.items):
        parsed = urlparse(item.url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in {"http", "https"}:
            errors.append(ValidationErrorItem(file="pack_manifest.json", path=f"items.{index}.url", message="url must be http or https"))
        if host and host not in settings.allowed_domains and not host.endswith(".convly.jp"):
            errors.append(
                ValidationErrorItem(
                    file="pack_manifest.json",
                    path=f"items.{index}.url",
                    message=f"host '{host}' is not an allowed manifest domain",
                )
            )

    return ValidationResult(valid=not errors, errors=errors)
