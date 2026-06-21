import re
import unicodedata


def normalize_tts_text(value: str) -> str:
    return str(value or "").strip()


def strip_choice_separator(value: str) -> str:
    text = str(value or "").strip()
    return re.sub(r"[、。，,\s]+$", "", text)


def strip_terminal_punctuation(value: str) -> str:
    text = str(value or "").strip()
    return re.sub(r"[、。．.,\s]+$", "", text)


_KATAKANA_TOKEN_RE = re.compile(r"[ァ-ヶー・]{2,}")
_KATAKANA_PAREN_RE = re.compile(r"([ァ-ヶー・]{2,})\s*([（(])\s*([ァ-ヶー・\s・ー]{2,})\s*([）)])")


def collapse_duplicate_katakana_utterances(value: str) -> str:
    text = collapse_duplicate_katakana_parentheticals(value)
    return _collapse_adjacent_katakana_runs(text)


def collapse_duplicate_katakana_parentheticals(value: str) -> str:
    text = str(value or "")
    return _KATAKANA_PAREN_RE.sub(_collapse_parenthetical_match, text)


def _collapse_parenthetical_match(match: re.Match) -> str:
    before = match.group(1)
    inside = match.group(3)
    return before if _same_katakana_reading(before, inside) else match.group(0)


def _collapse_adjacent_katakana_runs(value: str) -> str:
    def replace(match: re.Match) -> str:
        token = match.group(0)
        length = len(token)
        if length % 2:
            return token
        half = length // 2
        left = token[:half]
        right = token[half:]
        return left if _same_katakana_reading(left, right) else token

    previous = None
    current = value
    while previous != current:
        previous = current
        current = _KATAKANA_TOKEN_RE.sub(replace, current)
    return current


def _same_katakana_reading(left: str, right: str) -> bool:
    left_normalized = _normalize_katakana_reading(left)
    right_normalized = _normalize_katakana_reading(right)
    return bool(left_normalized and left_normalized == right_normalized)


def _normalize_katakana_reading(value: str) -> str:
    chars: list[str] = []
    for char in unicodedata.normalize("NFKC", value):
        if char.isspace() or unicodedata.category(char).startswith("P"):
            continue
        chars.append(char)
    return "".join(chars)
