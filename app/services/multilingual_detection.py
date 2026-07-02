from __future__ import annotations

import re
from enum import Enum
from typing import Any


class MultilingualStatus(str, Enum):
    MULTILINGUAL = "multilingual"
    NORMAL = "normal"
    UNKNOWN = "unknown"


_TTS_LANGUAGE_TAG_RE = re.compile(r"\[(?:[a-z]{2,3}-[A-Za-z0-9]{2,8}(?:-[A-Za-z0-9]{2,8})*)\]")


def detect_multilingual(file_data: Any) -> MultilingualStatus:
    if not isinstance(file_data, dict):
        return MultilingualStatus.UNKNOWN

    metadata_flag = _extract_multilingual_flag(file_data)
    if metadata_flag is True:
        return MultilingualStatus.MULTILINGUAL
    if metadata_flag is False:
        return MultilingualStatus.NORMAL

    if _contains_tts_language_tag(file_data):
        return MultilingualStatus.MULTILINGUAL

    if _has_multilingual_structure(file_data):
        return MultilingualStatus.MULTILINGUAL

    return MultilingualStatus.UNKNOWN


def _extract_multilingual_flag(file_data: dict[str, Any]) -> bool | None:
    direct = file_data.get("multilingual")
    parsed = _parse_bool(direct)
    if parsed is not None:
        return parsed

    metadata = file_data.get("metadata")
    if isinstance(metadata, dict):
        parsed = _parse_bool(metadata.get("multilingual"))
        if parsed is not None:
            return parsed

    return None


def _parse_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "y"}:
            return True
        if lowered in {"false", "0", "no", "n"}:
            return False
    return None


def _contains_tts_language_tag(file_data: dict[str, Any]) -> bool:
    return _scan_for_tts_language_tags(file_data)


def _scan_for_tts_language_tags(node: Any) -> bool:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "tts" and isinstance(value, dict) and _node_contains_language_tag(value):
                return True
            if _scan_for_tts_language_tags(value):
                return True
        return False
    if isinstance(node, list):
        return any(_scan_for_tts_language_tags(item) for item in node)
    return False


def _node_contains_language_tag(node: Any) -> bool:
    if isinstance(node, str):
        return bool(_TTS_LANGUAGE_TAG_RE.search(node))
    if isinstance(node, dict):
        return any(_node_contains_language_tag(value) for value in node.values())
    if isinstance(node, list):
        return any(_node_contains_language_tag(item) for item in node)
    return False


def _has_multilingual_structure(file_data: dict[str, Any]) -> bool:
    learning_language = file_data.get("learningLanguage")
    if isinstance(learning_language, str) and learning_language.strip():
        return True

    root_language_mode_keys = {
        "documentTextLanguageMode",
        "questionLanguageMode",
        "choicesLanguageMode",
        "explanationLanguageMode",
    }
    root_language_keys = {
        "documentTextLanguage",
        "questionLanguage",
        "choicesLanguage",
        "explanationLanguage",
    }
    if any(key in file_data for key in root_language_mode_keys | root_language_keys):
        return True

    return _scan_for_tts_language_fields(file_data)


def _scan_for_tts_language_fields(node: Any) -> bool:
    if isinstance(node, dict):
        tts = node.get("tts")
        if isinstance(tts, dict) and _tts_dict_has_language_fields(tts):
            return True
        return any(_scan_for_tts_language_fields(value) for value in node.values())
    if isinstance(node, list):
        return any(_scan_for_tts_language_fields(item) for item in node)
    return False


def _tts_dict_has_language_fields(tts: dict[str, Any]) -> bool:
    keys = {"questionLanguage", "choicesLanguage", "answerLanguage", "explanationLanguage"}
    for key in keys:
        value = tts.get(key)
        if isinstance(value, str) and value.strip():
            return True
    return False
