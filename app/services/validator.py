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
                pack = SokqaDocumentPack.model_validate(file.content)
                errors.extend(validate_document_semantics(file.name, pack))
            elif file.kind == "quiz":
                pack = SokqaQuizPack.model_validate(file.content)
                errors.extend(validate_quiz_semantics(file.name, pack))
            elif file.kind == "manifest":
                PackManifest.model_validate(file.content)
        except ValidationError as exc:
            _append_pydantic_errors(file.name, exc, errors)

    if manifest:
        errors.extend(validate_manifest(manifest).errors)

    return ValidationResult(valid=not errors, errors=errors)


def validate_document_semantics(file_name: str, pack: SokqaDocumentPack) -> list[ValidationErrorItem]:
    errors: list[ValidationErrorItem] = []
    for index, item in enumerate(pack.documents):
        text = item.text.strip()
        tts_text = (item.tts.text if item.tts and item.tts.text else "").strip()
        if not text:
            errors.append(ValidationErrorItem(file=file_name, path=f"documents.{index}.text", message="document text must not be empty"))
        if text == pack.title:
            errors.append(
                ValidationErrorItem(
                    file=file_name,
                    path=f"documents.{index}.text",
                    message="document text must not be only the chapter title",
                )
            )
        if item.id == text or text in {f"{pack.title} {index + 1}", f"{pack.title} 第{index + 1}章"}:
            errors.append(
                ValidationErrorItem(
                    file=file_name,
                    path=f"documents.{index}.text",
                    message="document text appears to be a placeholder",
                )
            )
        if item.tts is not None and not tts_text and not item.tts.audioUrl:
            errors.append(ValidationErrorItem(file=file_name, path=f"documents.{index}.tts", message="tts must contain text or audioUrl when present"))
        if tts_text and tts_text == pack.title:
            errors.append(
                ValidationErrorItem(
                    file=file_name,
                    path=f"documents.{index}.tts.text",
                    message="tts.text must not be only the chapter title",
                )
            )
    return errors


def validate_quiz_semantics(file_name: str, pack: SokqaQuizPack) -> list[ValidationErrorItem]:
    errors: list[ValidationErrorItem] = []
    if pack.questions:
        answer_indexes = {question.answerIndex for question in pack.questions}
        if len(answer_indexes) == 1 and len(pack.questions) > 1:
            errors.append(
                ValidationErrorItem(
                    file=file_name,
                    path="questions.answerIndex",
                    message="answerIndex must not be identical for every question",
                )
            )

        explanations = {question.explanation.strip() for question in pack.questions}
        if len(explanations) == 1 and len(pack.questions) > 1:
            errors.append(
                ValidationErrorItem(
                    file=file_name,
                    path="questions.explanation",
                    message="explanation must not be identical for every question",
                )
            )

    for index, question in enumerate(pack.questions):
        if question.question.strip() in {pack.title, f"{pack.title} {index + 1}"}:
            errors.append(
                ValidationErrorItem(
                    file=file_name,
                    path=f"questions.{index}.question",
                    message="question appears to be a placeholder",
                )
            )
        if len(question.choices) != 4 or not all(isinstance(choice, str) and choice.strip() for choice in question.choices):
            errors.append(
                ValidationErrorItem(
                    file=file_name,
                    path=f"questions.{index}.choices",
                    message="choices must be a 4-item string array",
                )
            )
    return errors


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
