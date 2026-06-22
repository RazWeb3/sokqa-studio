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

# 参照素材
以下は教材作成のための素材です。必要部分のみ参照してください。
出力JSONにこの見出しや指示文を混入させないでください。
{_quote_safety_instruction(text)}

{text}
""".strip()


def _quote_safety_instruction(source_text: str) -> str:
    if not _looks_like_quote_heavy_material(source_text):
        return ""
    return """
歌詞や引用文をJSON文字列に入れる場合、改行は短く整理してください。
歌詞全文を転載しないでください。
必要な短い引用だけを使い、引用は1セクションあたり1〜2行までにしてください。
""".strip()


def _looks_like_quote_heavy_material(source_text: str) -> bool:
    lines = [line.strip() for line in source_text.replace("\r\n", "\n").split("\n") if line.strip()]
    quote_marks = source_text.count("「") + source_text.count("」") + source_text.count('"') + source_text.count("'")
    return len(source_text) >= 1000 or len(lines) >= 12 or quote_marks >= 8
