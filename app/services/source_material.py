from app.schemas.common import SourceMode


def normalize_source(source_text: str | None, source_mode: SourceMode | None) -> tuple[str | None, SourceMode | None]:
    text = (source_text or "").strip()
    if not text:
        return None, None
    return text, source_mode or "document_reference"


def source_prompt_block(source_text: str | None, source_mode: SourceMode | None) -> str:
    text, mode = normalize_source(source_text, source_mode)
    if not text or not mode:
        return ""

    if mode == "document_only":
        instruction = (
            "Use only the content written in the reference material below to create the outline and learning content. "
            "Do not add facts, terms, examples, or claims that are not present in the material."
        )
    else:
        instruction = (
            "Use the reference material below as the primary foundation. You may supplement and flesh it out with your own knowledge "
            "when needed to make a natural learning material, but do not drift away from the material's intent."
        )

    return f"""
Reference material mode: {mode}
Reference material instruction:
{instruction}

# Reference material
{text}
""".strip()
