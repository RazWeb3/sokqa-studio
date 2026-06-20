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


def save_system_tts_rules(rules: list[TtsRule]) -> list[TtsRule]:
    settings = get_settings()
    saved = merge_tts_rules(rules)
    save_tts_rules_file(settings.tts_rules_path, saved)
    return saved


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
    return sort_tts_rules([TtsRule.model_validate(item) for item in data])


def save_tts_rules_file(path_value: str, rules: list[TtsRule]) -> None:
    if not path_value:
        raise ValueError("TTS rules path is not configured")
    path = Path(path_value)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = [
        {key: value for key, value in rule.model_dump().items() if value is not None}
        for rule in rules
    ]
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def merge_tts_rules(*rule_groups: list[TtsRule]) -> list[TtsRule]:
    merged: dict[str, TtsRule] = {}
    for rules in rule_groups:
        for rule in rules:
            merged[rule.source] = rule
    return sort_tts_rules(list(merged.values()))


def sort_tts_rules(rules: list[TtsRule]) -> list[TtsRule]:
    return [rule for _index, rule in sorted(enumerate(rules), key=lambda item: (-len(item[1].source), item[0]))]
