import re


_WORD_DOT_PLACEHOLDER = "\uE000"


def _preserve_word_dots(text: str) -> str:
    return re.sub(
        r"(?<=[A-Za-z0-9])\.(?=[A-Za-z0-9])|(?<![A-Za-z0-9])\.(?=[A-Za-z0-9])|(?<=[A-Za-z0-9])\.(?![A-Za-z0-9\s]|$)",
        _WORD_DOT_PLACEHOLDER,
        text,
    )


def normalize_tts_text(value: str) -> str:
    text = str(value or "").strip()
    text = _preserve_word_dots(text)
    text = text.replace("。", "、").replace("．", "、")
    text = re.sub(r"(?<![A-Za-z0-9])\.(?![A-Za-z0-9])|(?<=[A-Za-z0-9])\.(?=\s|$)", "、", text)
    text = text.replace(_WORD_DOT_PLACEHOLDER, ".")
    text = re.sub(r"\s*、\s*", "、", text)
    text = re.sub(r"、+", "、", text)
    text = text.strip(" \t\r\n、")
    return f"{text}、" if text else ""


def strip_terminal_punctuation(value: str) -> str:
    text = str(value or "").strip()
    return re.sub(r"[、。．.,\s]+$", "", text)
