"""Cloud Text-to-Speech synthesis helpers."""

from google.cloud import texttospeech

from app.config import get_settings


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
    response = client.synthesize_speech(
        input=texttospeech.SynthesisInput(text=text),
        voice=texttospeech.VoiceSelectionParams(
            language_code=language_code or settings.cloud_tts_language_code,
            name=voice_name or settings.cloud_tts_voice,
        ),
        audio_config=texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.MP3,
            speaking_rate=speaking_rate if speaking_rate is not None else settings.cloud_tts_speaking_rate,
            pitch=pitch if pitch is not None else settings.cloud_tts_pitch,
        ),
    )
    return response.audio_content
