"""Cloud Text-to-Speech synthesis helpers."""

from google.cloud import texttospeech

from app.config import get_settings
from app.services.tts_voices import is_chirp3_hd_voice


def synthesize_text_to_mp3(
    text: str,
    *,
    language_code: str | None = None,
    voice_name: str | None = None,
    speaking_rate: float | None = None,
    pitch: float | None = None,
) -> bytes:
    """Synthesize plain text to MP3 bytes using Google Cloud Text-to-Speech."""
    settings = get_settings()
    client = texttospeech.TextToSpeechClient()
    selected_voice_name = voice_name or settings.cloud_tts_voice
    audio_config_kwargs = _audio_config_kwargs(
        selected_voice_name,
        speaking_rate=speaking_rate,
        pitch=pitch,
        default_speaking_rate=settings.cloud_tts_speaking_rate,
        default_pitch=settings.cloud_tts_pitch,
    )
    response = client.synthesize_speech(
        input=texttospeech.SynthesisInput(text=text),
        voice=texttospeech.VoiceSelectionParams(
            language_code=language_code or settings.cloud_tts_language_code,
            name=selected_voice_name,
        ),
        audio_config=texttospeech.AudioConfig(**audio_config_kwargs),
    )
    return response.audio_content


def _audio_config_kwargs(
    voice_name: str,
    *,
    speaking_rate: float | None = None,
    pitch: float | None = None,
    default_speaking_rate: float = 1.0,
    default_pitch: float = 0.0,
) -> dict:
    selected_speaking_rate = speaking_rate if speaking_rate is not None else default_speaking_rate
    kwargs = {
        "audio_encoding": texttospeech.AudioEncoding.MP3,
        "speaking_rate": min(selected_speaking_rate, 2.0) if is_chirp3_hd_voice(voice_name) else selected_speaking_rate,
    }
    if not is_chirp3_hd_voice(voice_name):
        kwargs["pitch"] = pitch if pitch is not None else default_pitch
    return kwargs
