from __future__ import annotations

import re


ASCII_LETTER_RE = re.compile(r"[A-Za-z]")


def language_base(language: str | None) -> str:
    return (language or "").split("-")[0].lower()


def language_script(language: str | None) -> str:
    base = language_base(language)
    if base == "ja":
        return "japanese"
    if base == "zh":
        return "cjk"
    if base == "ko":
        return "hangul"
    if base in {"en", "es", "fr", "de", "it", "pt", "id"}:
        return "latin"
    if base in {"ru", "uk", "bg", "sr"}:
        return "cyrillic"
    if base in {"ar", "fa", "ur"}:
        return "arabic"
    if base == "th":
        return "thai"
    return "latin"


def scripts_in_text(text: str) -> set[str]:
    scripts: set[str] = set()
    has_han = False
    for char in text:
        code = ord(char)
        if 0x3040 <= code <= 0x30FF or 0x31F0 <= code <= 0x31FF:
            scripts.add("japanese")
        elif 0x3400 <= code <= 0x4DBF or 0x4E00 <= code <= 0x9FFF or 0xF900 <= code <= 0xFAFF:
            has_han = True
        elif 0x1100 <= code <= 0x11FF or 0x3130 <= code <= 0x318F or 0xAC00 <= code <= 0xD7AF:
            scripts.add("hangul")
        elif 0x00C0 <= code <= 0x024F:
            scripts.add("latin")
        elif 0x0400 <= code <= 0x052F:
            scripts.add("cyrillic")
        elif 0x0600 <= code <= 0x06FF:
            scripts.add("arabic")
        elif 0x0E00 <= code <= 0x0E7F:
            scripts.add("thai")
    if ASCII_LETTER_RE.search(text):
        scripts.add("latin")
    if has_han:
        scripts.add("japanese" if "japanese" in scripts else "cjk")
    return scripts


def leading_script(text: str) -> str | None:
    for char in text:
        code = ord(char)
        if char.isascii() and char.isalpha():
            return "latin"
        if 0x3040 <= code <= 0x30FF or 0x31F0 <= code <= 0x31FF:
            return "japanese"
        if 0x3400 <= code <= 0x4DBF or 0x4E00 <= code <= 0x9FFF or 0xF900 <= code <= 0xFAFF:
            return "cjk"
        if 0x1100 <= code <= 0x11FF or 0x3130 <= code <= 0x318F or 0xAC00 <= code <= 0xD7AF:
            return "hangul"
        if 0x00C0 <= code <= 0x024F:
            return "latin"
        if 0x0400 <= code <= 0x052F:
            return "cyrillic"
        if 0x0600 <= code <= 0x06FF:
            return "arabic"
        if 0x0E00 <= code <= 0x0E7F:
            return "thai"
    return None


def text_matches_language_script(text: str, language: str | None) -> bool:
    target = language_script(language)
    scripts = scripts_in_text(text)
    if target == "japanese":
        return "japanese" in scripts
    return target in scripts


def choice_set_language_state(choices: list[str], pack_language: str, learning_language: str | None) -> str:
    if not learning_language:
        return "unknown"
    pack_script = language_script(pack_language)
    learning_script = language_script(learning_language)
    if pack_script == learning_script:
        return "ambiguous"

    states: set[str] = set()
    for choice in choices:
        scripts = scripts_in_text(choice)
        if {pack_script, learning_script} == {"japanese", "cjk"} and scripts == {"cjk"}:
            states.add("unknown")
            continue
        has_pack = pack_script in scripts or (pack_script == "japanese" and "cjk" in scripts)
        has_learning = learning_script in scripts
        if learning_script == "japanese" and "cjk" in scripts:
            has_learning = True
        if has_pack and has_learning:
            states.add("mixed")
        elif has_learning:
            states.add("learning")
        elif has_pack:
            states.add("pack")
        else:
            states.add("unknown")
    meaningful = states - {"unknown"}
    if "mixed" in meaningful or len(meaningful) > 1:
        return "mixed"
    if meaningful == {"learning"}:
        return "learning"
    if meaningful == {"pack"}:
        return "pack"
    return "unknown"
