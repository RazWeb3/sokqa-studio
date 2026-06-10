from app.services.tts_synthesizer import _audio_config_kwargs


def test_chirp3_hd_audio_config_omits_pitch_and_caps_speaking_rate() -> None:
    kwargs = _audio_config_kwargs(
        "ja-JP-Chirp3-HD-Aoede",
        speaking_rate=3.0,
        pitch=-5.0,
        default_speaking_rate=1.0,
        default_pitch=0.0,
    )

    assert kwargs["speaking_rate"] == 2.0
    assert "pitch" not in kwargs


def test_neural2_audio_config_keeps_pitch_and_speaking_rate() -> None:
    kwargs = _audio_config_kwargs(
        "ja-JP-Neural2-B",
        speaking_rate=3.0,
        pitch=-5.0,
        default_speaking_rate=1.0,
        default_pitch=0.0,
    )

    assert kwargs["speaking_rate"] == 3.0
    assert kwargs["pitch"] == -5.0
