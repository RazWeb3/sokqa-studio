import json
import logging
from pathlib import Path

from app.config import get_settings
from app.schemas.common import TtsRule


logger = logging.getLogger("sokqa_course_pack_agent")


def load_configured_tts_rules() -> list[TtsRule]:
    path_value = get_settings().tts_rules_path
    if not path_value:
        return []
    path = Path(path_value)
    if not path.exists():
        logger.warning("TTS rules file not found; continuing with empty rules: %s", path)
        return []
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        logger.warning("TTS rules file is empty; continuing with empty rules: %s", path)
        return []
    data = json.loads(raw)
    if isinstance(data, dict):
        data = [{"source": source, "reading": reading} for source, reading in data.items()]
    return [TtsRule.model_validate(item) for item in data]
