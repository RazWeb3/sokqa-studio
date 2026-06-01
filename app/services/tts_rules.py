import json
import logging
from pathlib import Path

from app.config import get_settings
from app.schemas.common import TtsRule


logger = logging.getLogger("sokqa_course_pack_agent")


def load_configured_tts_rules() -> list[TtsRule]:
    settings = get_settings()
    system_rules = load_tts_rules_file(settings.tts_rules_path, "system")
    user_rules = load_tts_rules_file(settings.tts_user_rules_path, "user")
    return merge_tts_rules(system_rules, user_rules)


def load_system_tts_rules() -> list[TtsRule]:
    return load_tts_rules_file(get_settings().tts_rules_path, "system")


def load_user_tts_rules() -> list[TtsRule]:
    return load_tts_rules_file(get_settings().tts_user_rules_path, "user")


def load_tts_rules_file(path_value: str, label: str) -> list[TtsRule]:
    if not path_value:
        return []
    path = Path(path_value)
    if not path.exists():
        logger.warning("TTS %s rules file not found; continuing with empty rules: %s", label, path)
        return []
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        logger.warning("TTS %s rules file is empty; continuing with empty rules: %s", label, path)
        return []
    data = json.loads(raw)
    if isinstance(data, dict):
        data = [{"source": source, "reading": reading} for source, reading in data.items()]
    return [TtsRule.model_validate(item) for item in data]


def merge_tts_rules(*rule_groups: list[TtsRule]) -> list[TtsRule]:
    merged: dict[str, TtsRule] = {}
    for rules in rule_groups:
        for rule in rules:
            merged[rule.source] = rule
    return list(merged.values())
