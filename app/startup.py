import logging

from app.config import get_settings


logger = logging.getLogger("sokqa_course_pack_agent")


def log_runtime_settings() -> None:
    settings = get_settings()
    logger.info("Gemini provider: %s", settings.gemini_provider)
    logger.info("Gemini planner model: %s", settings.planner_model)
    logger.info("Gemini document model: %s", settings.document_model)
    logger.info("Gemini quiz model: %s", settings.quiz_model)
