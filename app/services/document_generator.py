import logging
import re
import time
from math import ceil

from app.schemas.common import SUPPORTED_PACK_LANGUAGES
from app.schemas.sokqa import CoursePlan, PlanDocument, SokqaDocumentItem, SokqaDocumentPack
from app.config import get_settings
from app.services.gemini_client import GeminiClient
from app.services.generation_status import record_generation_source
from app.services.llm_json import LlmJsonParseContext, LlmJsonParseError
from app.services.pack_ids import document_pack_id
from app.services.prompts import document_generation_prompt
from app.services.generation.context import GenerationContext
from app.services.tagging import document_global_tags
from app.services.tts_text import normalize_tts_text


STRICT_MAX_DOCUMENT_FILES = 50
STRICT_MAX_SECTIONS_PER_FILE = 50
LANGUAGE_TAG_RE = re.compile(r"\[(?:[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})+)\]")
# BCP-47 風の言語タグ（例: en-US, ja-JP, id-ID）のみを対象とする。
# 任意の「ハイフン付き英単語」（Wi-Fi, X-ray, T-shirt, e-mail, step-by-step 等）を
# 言語コードと誤認して削除しないよう、アプリが扱う言語のベースコードのみをホワイトリスト化する。
_LANGUAGE_BASE_CODES = "|".join(sorted(SUPPORTED_PACK_LANGUAGES.keys()))
LANGUAGE_CODE_RE = re.compile(
    rf"(?<![A-Za-z0-9])"
    rf"(?:{_LANGUAGE_BASE_CODES})"
    rf"-[A-Za-z0-9]{{2,8}}"
    rf"(?![A-Za-z0-9])"
)
logger = logging.getLogger(__name__)

_JSON_RETRY_INSTRUCTION = """
The previous response was not valid JSON.
Return the complete document again as valid JSON only.
Do not use Markdown code fences.
Do not include comments or explanatory text.
Escape all quotation marks inside string values.
Ensure every property has a colon and every array item is comma-separated.
""".strip()


def _source_paragraphs(source_text: str) -> list[str]:
    paragraphs = [part.strip() for part in source_text.replace("\r\n", "\n").split("\n\n")]
    return [paragraph for paragraph in paragraphs if paragraph]


def sanitize_learner_facing_text(value: str) -> str:
    cleaned = LANGUAGE_TAG_RE.sub("", value)
    cleaned = LANGUAGE_CODE_RE.sub("", cleaned)
    cleaned = re.sub(r"[^\S\r\n]+", " ", cleaned)
    cleaned = re.sub(r"[^\S\r\n]*\n[^\S\r\n]*", "\n", cleaned)
    cleaned = re.sub(r"\s+([、。！？!?,.;:)\]】」』）])", r"\1", cleaned)
    cleaned = re.sub(r"([(\[【「『（])\s+", r"\1", cleaned)
    return cleaned.strip()


def _balanced_chunks(items: list[str], chunk_count: int) -> list[list[str]]:
    if not items:
        return []
    chunk_count = max(1, min(chunk_count, len(items)))
    base_size, remainder = divmod(len(items), chunk_count)
    chunks: list[list[str]] = []
    start = 0
    for index in range(chunk_count):
        size = base_size + (1 if index < remainder else 0)
        end = start + size
        chunks.append(items[start:end])
        start = end
    return chunks


def strict_source_paragraphs(source_text: str) -> list[str]:
    return _source_paragraphs(source_text)


def split_strict_source_sections(paragraphs: list[str]) -> list[list[str]]:
    if not paragraphs:
        return []
    file_count = max(1, ceil(len(paragraphs) / STRICT_MAX_SECTIONS_PER_FILE))
    return _balanced_chunks(paragraphs, file_count)


def strict_source_file_count(source_text: str) -> int:
    paragraphs = strict_source_paragraphs(source_text)
    if not paragraphs and source_text.strip():
        paragraphs = [source_text.strip()]
    return len(split_strict_source_sections(paragraphs))


def strict_source_limit_error(file_count: int) -> str | None:
    if file_count <= STRICT_MAX_DOCUMENT_FILES:
        return None
    return (
        f"資料が大きすぎます（推定{file_count}ファイル）。"
        f"{STRICT_MAX_DOCUMENT_FILES}ファイル以内に収まるよう資料を分割して投入してください。"
    )


def generate_strict_source_document_pack(
    plan: CoursePlan,
    document: PlanDocument,
    sections: list[str] | None = None,
) -> SokqaDocumentPack:
    source_text = (plan.sourceText or "").strip()
    sections = sections if sections is not None else (_source_paragraphs(source_text) or [source_text])
    items = [
        SokqaDocumentItem(id=f"doc-{index}", text=text)
        for index, text in enumerate(sections, start=1)
        if text.strip()
    ]
    if not items:
        items = [SokqaDocumentItem(id="doc-1", text=document.goal or document.title)]
    record_generation_source("source", f"Document {document.id} copied from source material without LLM rewriting")
    return SokqaDocumentPack(
        id=document_pack_id(plan, document),
        title=document.title,
        description=document.goal,
        language=plan.language,
        learningLanguage=plan.learningLanguage,
        author=plan.author,
        globalTags=document_global_tags(plan, document),
        documents=items,
    )


def generate_document_pack(
    plan: CoursePlan, document: PlanDocument, model: str | None = None, *, context: GenerationContext | None = None
) -> SokqaDocumentPack:
    if get_settings().gemini_provider == "gemini":
        try:
            settings = get_settings()
            prompt = document_generation_prompt(plan, document, context=context)
            content = _generate_document_json_with_retry(
                prompt,
                document=document,
                model=model or settings.document_model,
                plan=plan,
            )
            content = normalize_document_content(content, plan, document)
            pack = SokqaDocumentPack.model_validate(content)
            record_generation_source("gemini", f"Document {document.id} generated by Gemini ({model or settings.document_model})")
            _log_generated_units(pack, model or settings.document_model)
            return pack
        except Exception as exc:
            record_generation_source("gemini", f"Document {document.id} generation failed: {exc}")
            raise RuntimeError(
                f"ドキュメント生成に失敗しました（{document.id}: {document.title}）。"
                "失敗時テンプレートの混入を防ぐため、この生成結果は保存していません。"
                f"詳細: {exc}"
            ) from exc

    record_generation_source("mock", f"Document {document.id} generated by mock")
    return generate_mock_document_pack(plan, document)


def _generate_document_json_with_retry(
    prompt: str,
    *,
    document: PlanDocument,
    model: str,
    plan: CoursePlan,
    attempts: int = 3,
) -> dict:
    last_error: Exception | None = None
    delays = (0.5, 1.5)
    for attempt in range(1, attempts + 1):
        retrying = attempt > 1
        try:
            attempt_prompt = _document_retry_prompt(prompt) if retrying else prompt
        except Exception as exc:
            # Keep this distinct from a model/JSON failure: it proves the
            # second request was never sent and preserves the traceback.
            logger.exception(
                "document_generation.retry_prompt_failed file_id=%s attempt=%s max_attempts=%s model=%s exception_type=%s",
                document.id, attempt, attempts, model, type(exc).__name__,
            )
            raise RuntimeError(f"document retry prompt creation failed: {document.id}") from exc
        logger.info(
            "document_generation.attempt_started file_id=%s attempt=%s max_attempts=%s model=%s retrying=%s response_received=%s parse_started=%s",
            document.id, attempt, attempts, model, retrying, False, False,
        )
        try:
            result = GeminiClient().generate_json(
                attempt_prompt,
                model=model,
                response_schema=SokqaDocumentPack,
                parse_context=LlmJsonParseContext(
                    generation_unit="doc",
                    model=model,
                    theme=plan.title,
                    doc_id=document.id,
                    title=document.title,
                    source_text=plan.sourceText,
                    additional_instructions=plan.customInstructions,
                    tts_reading_mode=plan.ttsReadingMode,
                    language=plan.language,
                    difficulty=plan.difficulty,
                    scale=plan.scale,
                ),
            )
            logger.info(
                "document_generation.attempt_succeeded file_id=%s attempt=%s max_attempts=%s model=%s response_received=%s parse_started=%s",
                document.id, attempt, attempts, model, True, True,
            )
            return result
        except Exception as exc:
            last_error = exc
            parse = exc if isinstance(exc, LlmJsonParseError) else None
            response_received = isinstance(exc, LlmJsonParseError)
            logger.warning(
                "document_generation.%s file_id=%s attempt=%s max_attempts=%s model=%s response_received=%s parse_started=%s exception_type=%s json_parse_line=%s json_parse_column=%s char_position=%s error=%s",
                "failed" if attempt >= attempts else "retry", document.id, attempt, attempts, model, response_received, response_received, type(exc).__name__,
                _json_error_position(parse)[0], _json_error_position(parse)[1], _json_error_position(parse)[2], repr(exc),
            )
            if attempt >= attempts:
                break
            time.sleep(delays[min(attempt - 1, len(delays) - 1)])
    raise last_error or RuntimeError(f"document generation failed: {document.id}")


def _document_retry_prompt(prompt: str) -> str:
    """Reissue the same request; never feed the malformed response back in."""
    return f"{prompt}\n\n{_JSON_RETRY_INSTRUCTION}"


def _json_error_position(error: LlmJsonParseError | None) -> tuple[str, str, str]:
    if not error or not error.attempts:
        return "", "", ""
    message = error.attempts[-1].get("error", "")
    match = re.search(r"line (\d+) column (\d+) \(char (\d+)\)", message)
    return match.groups() if match else ("", "", "")


def generate_mock_document_pack(plan: CoursePlan, document: PlanDocument) -> SokqaDocumentPack:
    items: list[SokqaDocumentItem] = []
    source_excerpt = (plan.sourceText or "").strip()
    for index in range(1, document.targetSectionCount + 1):
        point = document.keyPoints[(index - 1) % len(document.keyPoints)]
        if source_excerpt and plan.sourceMode == "document_only":
            text = f"{source_excerpt[:240]}。"
        elif source_excerpt and plan.sourceMode == "document_reference":
            if plan.structurePolicy == "listening":
                text = (
                    f"まず資料の内容を手がかりに、{point}を流れの中で確認していきます。"
                    f"{source_excerpt[:160]}。"
                    f"ここでは細かな用語を並べるのではなく、前後の関係が耳で追えるように整理します。"
                )
            else:
                text = (
                    f"{source_excerpt[:180]}。"
                    f"{point}について、{plan.targetUser}にも分かるように補足して整理します。"
                    f"{plan.title}では、資料の趣旨を土台にして学習しやすい順序で理解します。"
                )
        else:
            if plan.structurePolicy == "listening":
                previous_hint = "前の話を受けて、" if index > 1 else ""
                text = (
                    f"{previous_hint}{point}を、具体的な場面に結びつけて考えてみます。"
                    f"いきなり定義を覚えるよりも、なぜそれが必要になるのかを順番にたどると理解しやすくなります。"
                    f"この流れを押さえると、{plan.title}の次の説明も自然につながって聞こえます。"
                )
            else:
                text = (
                    f"{document.title}のセクション{index}です。"
                    f"{point}について、{plan.targetUser}にも分かるように短く確認します。"
                    f"{plan.title}では、用語の意味と実際の使われ方を結びつけて覚えることが大切です。"
                )
        items.append(SokqaDocumentItem(id=f"doc-{index}", text=text))

    return SokqaDocumentPack(
        id=document_pack_id(plan, document),
        title=document.title,
        description=document.goal,
        language=plan.language,
        learningLanguage=plan.learningLanguage,
        author=plan.author,
        globalTags=document_global_tags(plan, document),
        documents=items,
    )


def normalize_document_content(content: dict | list, plan: CoursePlan, document: PlanDocument) -> dict:
    """Normalize either an object response or a bare document-unit array.

    Some models return the requested collection directly.  That is valid JSON
    content, so preserve it by assigning it to ``documents`` rather than
    treating the response as a parser failure.
    """
    if isinstance(content, list):
        normalized = {"documents": content}
    elif isinstance(content, dict):
        normalized = dict(content)
    else:
        raise ValueError("document response must be a JSON object or a documents array")
    normalized["id"] = document_pack_id(plan, document)
    normalized.setdefault("type", "document")
    normalized.setdefault("schemaVersion", 1)
    normalized.setdefault("title", document.title)
    normalized.setdefault("description", document.goal)
    normalized.setdefault("language", plan.language)
    normalized["learningLanguage"] = plan.learningLanguage
    normalized.setdefault("author", plan.author)
    normalized["globalTags"] = document_global_tags(plan, document)

    documents = normalized.get("documents")
    if documents is None:
        documents = normalized.get("sections") or normalized.get("items") or []

    fixed_documents = []
    for index, item in enumerate(documents, start=1):
        if not isinstance(item, dict):
            item = {"text": str(item)}
        text = item.get("text") or item.get("body") or item.get("content")
        if not text:
            title = item.get("title", "")
            summary = item.get("summary") or item.get("description") or ""
            text = f"{title}。{summary}".strip("。")
        fixed = dict(item)
        fixed["id"] = f"doc-{index}"
        fixed["text"] = sanitize_learner_facing_text(str(text))
        fixed.pop("tts", None)
        fixed.pop("tags", None)
        fixed.pop("body", None)
        fixed.pop("content", None)
        fixed_documents.append(fixed)

    normalized["documents"] = fixed_documents
    return normalized


# 生成直後の中間物を追跡可能にするため、ユニット単位で fingerprint を記録する。
# 固定サンプル名・全文ダンプは避け、破損検出に必要な範囲(id・文字数・先頭 fingerprint)に絞る。
_LOG_FINGERPRINT_LENGTH = 40


def _log_generated_units(pack: SokqaDocumentPack, model: str) -> None:
    for item in pack.documents:
        text = item.text or ""
        fingerprint = text[:_LOG_FINGERPRINT_LENGTH].replace("\n", " ")
        logger.info(
            "generation.document_produced file=%s unit_id=%s model=%s chars=%s fingerprint=%r",
            pack.id,
            item.id,
            model,
            len(text),
            fingerprint,
        )
