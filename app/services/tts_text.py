import re


def normalize_tts_text(value: str) -> str:
    return str(value or "").strip()


def strip_choice_separator(value: str) -> str:
    text = str(value or "").strip()
    return re.sub(r"[、。，,\s]+$", "", text)


def strip_terminal_punctuation(value: str) -> str:
    text = str(value or "").strip()
    return re.sub(r"[、。．.,\s]+$", "", text)
