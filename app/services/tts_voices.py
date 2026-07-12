"""Voice listing helpers for Google Cloud Text-to-Speech."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from google.cloud import texttospeech

_CACHE_TTL_SECONDS = 30 * 60
_VOICE_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}


# These labels are Sokqa's own product-facing grouping, not Google Cloud's
# official voice taxonomy. Keep the Google voice name as the source of truth.
@dataclass(frozen=True)
class SokqaVoice:
    name: str
    label: str
    gender: str
    tier: str
    order: int


SOKQA_VOICES: tuple[SokqaVoice, ...] = (
    SokqaVoice("ja-JP-Chirp3-HD-Aoede", "学習向け（女性）Aoede", "female", "high", 10),
    SokqaVoice("ja-JP-Chirp3-HD-Orus", "ビジネス向け（男性）Orus", "male", "high", 20),
    SokqaVoice("ja-JP-Chirp3-HD-Zephyr", "朗読向け（女性）Zephyr", "female", "high", 30),
    SokqaVoice("ja-JP-Chirp3-HD-Charon", "朗読向け（男性）Charon", "male", "high", 40),
    SokqaVoice("ja-JP-Neural2-B", "標準（開発用）Neural2", "female", "standard", 50),
    SokqaVoice("en-US-Chirp3-HD-Aoede", "学習向け（女性）Aoede", "female", "high", 10),
    SokqaVoice("en-US-Chirp3-HD-Orus", "ビジネス向け（男性）Orus", "male", "high", 20),
    SokqaVoice("en-US-Chirp3-HD-Zephyr", "朗読向け（女性）Zephyr", "female", "high", 30),
    SokqaVoice("en-US-Chirp3-HD-Charon", "朗読向け（男性）Charon", "male", "high", 40),
)

_LINKED_CHIRP3_HD_LOCALES = frozenset({"ja-JP", "en-US"})
_DEFAULT_LINKED_VOICE_BY_LOCALE = {
    "ja-JP": "ja-JP-Chirp3-HD-Aoede",
    "en-US": "en-US-Chirp3-HD-Aoede",
}

def is_chirp3_hd_voice(voice_name: str | None) -> bool:
    return bool(voice_name and "-Chirp3-HD-" in voice_name)


def linked_voice_for_language(voice_name: str | None, language_code: str) -> str | None:
    """Return the same adopted Chirp3 speaker in another supported locale.

    We deliberately map only the four product-adopted high-quality voices. A
    Neural2 fallback cannot promise that the same speaker exists in both
    languages, so callers retain their requested voice instead.
    """
    if not voice_name or language_code not in _LINKED_CHIRP3_HD_LOCALES:
        return None
    for voice in SOKQA_VOICES:
        suffix = voice.name.split("-", 2)[-1]
        if voice_name.endswith(f"-{suffix}") and "-Chirp3-HD-" in voice_name:
            return f"{language_code}-{suffix}"
    return None


def default_linked_voice_for_language(language_code: str) -> str | None:
    """Return the product default for a tagged bilingual recording."""
    return _DEFAULT_LINKED_VOICE_BY_LOCALE.get(language_code)


def filter_sokqa_voices(voices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only Sokqa's adopted voices and attach product labels."""
    by_name = {voice.get("name"): voice for voice in voices}
    adopted: list[dict[str, Any]] = []
    for metadata in SOKQA_VOICES:
        voice = by_name.get(metadata.name)
        if not voice:
            continue
        adopted.append(
            {
                **voice,
                "label": metadata.label,
                "gender": metadata.gender,
                "tier": metadata.tier,
                "order": metadata.order,
            }
        )
    return adopted


def list_cloud_tts_voices(language_code: str | None = None) -> list[dict[str, Any]]:
    """Return Sokqa-adopted Google Cloud TTS voices, cached briefly by language."""
    cache_key = language_code or ""
    now = time.monotonic()
    cached = _VOICE_CACHE.get(cache_key)
    if cached and now - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]

    client = texttospeech.TextToSpeechClient()
    response = client.list_voices(language_code=language_code)
    voices = [
        {
            "name": voice.name,
            "languageCodes": list(voice.language_codes),
            "ssmlGender": texttospeech.SsmlVoiceGender(voice.ssml_gender).name,
            "naturalSampleRateHertz": voice.natural_sample_rate_hertz,
        }
        for voice in response.voices
    ]
    voices = filter_sokqa_voices(voices)
    _VOICE_CACHE[cache_key] = (now, voices)
    return voices
