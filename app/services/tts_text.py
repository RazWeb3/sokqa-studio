import re


def normalize_tts_text(value: str) -> str:
    text = str(value or "").strip()
    text = text.replace("。", "、").replace("．", "、").replace(".", "、")
    text = re.sub(r"\s*、\s*", "、", text)
    text = re.sub(r"、+", "、", text)
    text = text.strip(" \t\r\n、")
    return f"{text}、" if text else ""


def strip_terminal_punctuation(value: str) -> str:
    text = str(value or "").strip()
    return re.sub(r"[、。．.,\s]+$", "", text)
