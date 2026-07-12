import logging
import re
import unicodedata
from urllib.parse import urlparse

from pydantic import ValidationError

from app.config import get_settings
from app.schemas.pack_v2 import PackManifestV2
from app.schemas.sokqa import (
    GeneratedFile,
    SokqaDocumentPack,
    SokqaQuizPack,
    ValidationErrorItem,
    ValidationResult,
)
from app.services.language_detection import (
    choice_set_language_state,
    language_script,
    scripts_in_text,
)
from app.services.generation.context import GenerationContext

logger = logging.getLogger(__name__)

QUIZ_CITATION_STYLE_PHRASES = (
    "ドキュメントでは",
    "ドキュメントによると",
    "資料によると",
    "と記載されています",
    "記載されています",
    "と述べられています",
    "述べられています",
    "と書かれています",
    "書かれています",
    "推奨されています",
    "とされています",
    "と説明されています",
    "説明されています",
    "と挙げられています",
    "挙げられています",
    "求められるとされています",
    "繋がるとされています",
    "過言ではないとされています",
)

# Learner-facing square-bracket labels are unfinished authoring placeholders.
# TTS language tags are stored in tts fields and are deliberately not inspected here.
_UNRESOLVED_PLACEHOLDER_RE = re.compile(
    r"\[(?:国名|都市名|数量|品物|もの|氏名|飲み物|番号|国|名前|目的地|交通手段|商品名|サイズ|ブランド名|特性|Name|Place|Company Name|destination|name|number|country|city|item)\]"
    r"|(?:〇〇|◯◯|○○|△△|××|□□|\b(?:TODO|FIXME|TBD)\b)",
    re.IGNORECASE,
)


def _canonical_display_text(text: str) -> str:
    """Exact-duplication key for display content, preserving meaningful wording."""
    normalized = unicodedata.normalize("NFKC", text or "").strip()
    return re.sub(r"\s+", " ", normalized)


def _append_placeholder_errors(
    errors: list[ValidationErrorItem], *, file_name: str, path: str, text: str
) -> None:
    for match in _UNRESOLVED_PLACEHOLDER_RE.finditer(text or ""):
        errors.append(
            ValidationErrorItem(
                file=file_name,
                path=path,
                message=f"unresolved learner-facing placeholder: {match.group(0)}",
                severity="error",
            )
        )


def _append_pydantic_errors(file_name: str, error: ValidationError, errors: list[ValidationErrorItem]) -> None:
    for issue in error.errors():
        path = ".".join(str(part) for part in issue.get("loc", [])) or "$"
        message = issue.get("msg", "invalid value")
        if _is_quality_pydantic_error(path, message):
            errors.append(ValidationErrorItem(file=file_name, path=path, message=message, severity="warning", classification="quality"))
        else:
            errors.append(ValidationErrorItem(file=file_name, path=path, message=message))


def _is_quality_pydantic_error(path: str, message: str) -> bool:
    """Some legacy model validators encode content quality rules as Pydantic errors.

    Keep malformed shapes and missing required structures technical, while
    preserving readable quiz content failures as reviewable diagnostics.
    """
    quality_messages = (
        "question must not be empty",
        "choices must contain exactly 4 items",
        "choices must not contain exact duplicates",
        "answerIndex must be between 0 and 3",
        "explanation must not be empty",
    )
    return path.startswith("questions.") and any(marker in message for marker in quality_messages)


def _as_quality_issues(issues: list[ValidationErrorItem]) -> list[ValidationErrorItem]:
    """Mark readable-but-incomplete learning content as reviewable quality issues."""
    return [issue.model_copy(update={"severity": "warning", "classification": "quality"}) for issue in issues]


def validate_files(
    files: list[GeneratedFile], manifest: PackManifestV2 | None = None, *, context: GenerationContext | None = None
) -> ValidationResult:
    errors: list[ValidationErrorItem] = []
    for file in files:
        try:
            if file.kind == "document":
                pack = SokqaDocumentPack.model_validate(file.content)
                errors.extend(_as_quality_issues(validate_document_semantics(file.name, pack, context=context)))
            elif file.kind == "quiz":
                pack = SokqaQuizPack.model_validate(file.content)
                errors.extend(_as_quality_issues(validate_quiz_semantics(file.name, pack, context=context)))
            elif file.kind == "manifest":
                PackManifestV2.model_validate(file.content)
        except ValidationError as exc:
            _append_pydantic_errors(file.name, exc, errors)

    if manifest:
        errors.extend(validate_manifest(manifest).errors)
        errors.extend(_manifest_reference_errors(files, manifest))

    return ValidationResult(valid=not blocking_errors_from_issues(errors), errors=errors)


def blocking_errors(validation: ValidationResult) -> list[ValidationErrorItem]:
    """Return only failures that make the pack unreadable, unresolvable, or unsaveable."""
    return blocking_errors_from_issues(validation.errors)


def blocking_errors_from_issues(issues: list[ValidationErrorItem]) -> list[ValidationErrorItem]:
    return [issue for issue in issues if issue.classification == "technical"]


def quality_issues(validation: ValidationResult) -> list[ValidationErrorItem]:
    return [issue for issue in validation.errors if issue.classification == "quality"]


def file_validation_status(validation: ValidationResult) -> str:
    if blocking_errors(validation):
        return "blocked"
    if validation.errors:
        return "warning"
    return "valid"


def _manifest_reference_errors(files: list[GeneratedFile], manifest: PackManifestV2) -> list[ValidationErrorItem]:
    """A manifest must resolve exactly the generated document and quiz files."""
    errors: list[ValidationErrorItem] = []
    files_by_name = {file.name: file for file in files if file.kind in {"document", "quiz"}}
    manifest_by_name = {item.name: item for item in manifest.items}
    for name, file in files_by_name.items():
        item = manifest_by_name.get(name)
        if item is None:
            errors.append(ValidationErrorItem(file="pack_manifest.json", path="items", message=f"manifest is missing generated file reference: {name}"))
        elif item.kind != file.kind:
            errors.append(ValidationErrorItem(file="pack_manifest.json", path="items", message=f"manifest kind does not match generated file: {name}"))
    for name in manifest_by_name:
        if name not in files_by_name:
            errors.append(ValidationErrorItem(file="pack_manifest.json", path="items", message=f"manifest references unavailable file: {name}"))
    return errors


def validate_document_semantics(
    file_name: str, pack: SokqaDocumentPack, *, context: GenerationContext | None = None
) -> list[ValidationErrorItem]:
    errors: list[ValidationErrorItem] = []
    if context and context.is_language_learning:
        _append_learning_language_presence_issues(file_name, pack, errors, context=context)
    seen_texts: dict[str, int] = {}
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
        _append_placeholder_errors(errors, file_name=file_name, path=f"documents.{index}.text", text=text)
        duplicate_of = seen_texts.get(_canonical_display_text(text)) if text else None
        if duplicate_of is not None:
            errors.append(
                ValidationErrorItem(
                    file=file_name,
                    path=f"documents.{index}.text",
                    message=f"document text exactly duplicates documents.{duplicate_of}.text; intentional repetition requires review",
                    severity="warning",
                )
            )
        elif text:
            seen_texts[_canonical_display_text(text)] = index
        if item.tts is not None and not tts_text and not item.tts.audioUrl and not item.tts.audioPath:
            errors.append(ValidationErrorItem(file=file_name, path=f"documents.{index}.tts", message="tts must contain text, audioPath, or audioUrl when present"))
        if tts_text and tts_text == pack.title:
            errors.append(
                ValidationErrorItem(
                    file=file_name,
                    path=f"documents.{index}.tts.text",
                    message="tts.text must not be only the chapter title",
                )
            )
    return errors


def _append_learning_language_presence_issues(
    file_name: str, pack: SokqaDocumentPack, errors: list[ValidationErrorItem], *, context: GenerationContext
) -> None:
    """Avoid treating brief bridges and summaries as failed learning sections.

    Until section roles are modelled, only whole-document absence is blocking.
    A long pack-language-only section remains a review warning.
    """
    learning_language = context.learning_language
    if not learning_language:
        return
    learning_script = language_script(learning_language)
    pack_script = language_script(pack.language)
    if not learning_script or learning_script == pack_script:
        return
    missing_indexes = [
        index for index, item in enumerate(pack.documents)
        if learning_script not in scripts_in_text(item.text or "")
    ]
    if len(missing_indexes) == len(pack.documents) and pack.documents:
        errors.append(
            ValidationErrorItem(
                file=file_name,
                path="documents",
                message=f"document must present the learning language ({learning_language}); no learning-language phrase found anywhere",
                severity="error",
            )
        )
        return
    for index in missing_indexes:
        text = pack.documents[index].text or ""
        if len(_canonical_display_text(text)) >= 120:
            errors.append(
                ValidationErrorItem(
                    file=file_name,
                    path=f"documents.{index}.text",
                    message=f"long document section has no learning-language phrase ({learning_language}); review its learning role",
                    severity="warning",
                )
            )


def validate_quiz_semantics(
    file_name: str, pack: SokqaQuizPack, *, context: GenerationContext | None = None
) -> list[ValidationErrorItem]:
    errors: list[ValidationErrorItem] = []
    seen_questions: dict[str, int] = {}
    if context and context.is_language_learning and context.learning_language:
        for index, question in enumerate(pack.questions):
            state = choice_set_language_state(question.choices, context.pack_language, context.learning_language)
            if pack.choiceLanguageMode == "auto" and state == "mixed":
                errors.append(
                    ValidationErrorItem(
                        file=file_name,
                        path=f"questions.{index}.choices",
                        message="auto choice language mode requires all four choices in one question to use the same language",
                        severity="error",
                    )
                )
            # Single-letter choice labels (A/B/C/D) are neutral notation, not
            # learner-language content.  The generator's targeted repair has
            # the stricter detector; this persistence gate avoids false
            # positives for imported or manually authored quizzes.
            has_substantive_choice = any(len(re.sub(r"\W+", "", choice)) > 1 for choice in question.choices)
            if pack.choiceLanguageMode == "pack" and state in {"learning", "mixed"} and has_substantive_choice:
                errors.append(
                    ValidationErrorItem(
                        file=file_name,
                        path=f"questions.{index}.choices",
                        message="pack choice language mode requires every choice to use the pack language after repair",
                        severity="error",
                    )
                )
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
        if _has_regular_answer_index_cycle(pack):
            errors.append(
                ValidationErrorItem(
                    file=file_name,
                    path="questions.answerIndex",
                    message="answerIndex should not follow a fully predictable cycle",
                    severity="warning",
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
        question_text = question.question.strip()
        _append_placeholder_errors(errors, file_name=file_name, path=f"questions.{index}.question", text=question_text)
        _append_placeholder_errors(errors, file_name=file_name, path=f"questions.{index}.explanation", text=question.explanation)
        duplicate_of = seen_questions.get(_canonical_display_text(question_text)) if question_text else None
        if duplicate_of is not None:
            errors.append(
                ValidationErrorItem(
                    file=file_name,
                    path=f"questions.{index}.question",
                    message=f"quiz question exactly duplicates questions.{duplicate_of}.question",
                    severity="error",
                )
            )
        elif question_text:
            seen_questions[_canonical_display_text(question_text)] = index
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
            continue
        seen_choices: dict[str, int] = {}
        for choice_index, choice in enumerate(question.choices):
            _append_placeholder_errors(
                errors,
                file_name=file_name,
                path=f"questions.{index}.choices.{choice_index}",
                text=choice,
            )
            duplicate_choice = seen_choices.get(_canonical_display_text(choice))
            if duplicate_choice is not None:
                errors.append(
                    ValidationErrorItem(
                        file=file_name,
                        path=f"questions.{index}.choices.{choice_index}",
                        message=f"choice exactly duplicates choices.{duplicate_choice} in the same question",
                        severity="error",
                    )
                )
            else:
                seen_choices[_canonical_display_text(choice)] = choice_index
        if 0 <= question.answerIndex < len(question.choices):
            answer = _canonical_display_text(question.choices[question.answerIndex])
            for choice_index, choice in enumerate(question.choices):
                if choice_index != question.answerIndex and _canonical_display_text(choice) == answer:
                    errors.append(
                        ValidationErrorItem(
                            file=file_name,
                            path=f"questions.{index}.choices.{choice_index}",
                            message="a distractor exactly matches the correct answer",
                            severity="error",
                        )
                    )
    errors.extend(quiz_citation_style_warnings(file_name, pack))
    return errors


def _has_regular_answer_index_cycle(pack: SokqaQuizPack) -> bool:
    indexes = [question.answerIndex for question in pack.questions]
    option_count = 4
    if len(indexes) < option_count * 2:
        return False
    return any(
        all(answer_index == (index + offset) % option_count for index, answer_index in enumerate(indexes))
        for offset in range(option_count)
    )


def quiz_citation_style_warnings(file_name: str, pack: SokqaQuizPack) -> list[ValidationErrorItem]:
    warnings: list[ValidationErrorItem] = []
    for index, question in enumerate(pack.questions):
        warning = _citation_style_warning(file_name, f"questions.{index}.question", question.question)
        if warning:
            warnings.append(warning)
        for choice_index, choice in enumerate(question.choices):
            warning = _citation_style_warning(file_name, f"questions.{index}.choices.{choice_index}", choice)
            if warning:
                warnings.append(warning)
        warning = _citation_style_warning(file_name, f"questions.{index}.explanation", question.explanation)
        if warning:
            warnings.append(warning)
    return warnings


def _citation_style_warning(file_name: str, path: str, text: str) -> ValidationErrorItem | None:
    for phrase in QUIZ_CITATION_STYLE_PHRASES:
        if phrase in text:
            logger.warning(
                "quiz citation-style wording detected file=%s path=%s phrase=%s",
                file_name,
                path,
                phrase,
            )
            return ValidationErrorItem(
                file=file_name,
                path=path,
                message=f"citation-style wording should be rewritten for direct learner-facing style: {phrase}",
                severity="warning",
            )
    return None


def validate_manifest(manifest: PackManifestV2) -> ValidationResult:
    settings = get_settings()
    errors: list[ValidationErrorItem] = []
    try:
        PackManifestV2.model_validate(manifest.model_dump())
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
