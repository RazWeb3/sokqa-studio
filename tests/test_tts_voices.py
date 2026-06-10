from app.services.tts_voices import filter_sokqa_voices


def test_filter_sokqa_voices_returns_adopted_voices_with_labels_in_order() -> None:
    voices = filter_sokqa_voices(
        [
            {
                "name": "ja-JP-Neural2-B",
                "languageCodes": ["ja-JP"],
                "ssmlGender": "FEMALE",
                "naturalSampleRateHertz": 24000,
            },
            {
                "name": "ja-JP-Wavenet-A",
                "languageCodes": ["ja-JP"],
                "ssmlGender": "FEMALE",
                "naturalSampleRateHertz": 24000,
            },
            {
                "name": "ja-JP-Chirp3-HD-Orus",
                "languageCodes": ["ja-JP"],
                "ssmlGender": "MALE",
                "naturalSampleRateHertz": 24000,
            },
            {
                "name": "ja-JP-Chirp3-HD-Aoede",
                "languageCodes": ["ja-JP"],
                "ssmlGender": "FEMALE",
                "naturalSampleRateHertz": 24000,
            },
        ]
    )

    assert [voice["name"] for voice in voices] == [
        "ja-JP-Chirp3-HD-Aoede",
        "ja-JP-Chirp3-HD-Orus",
        "ja-JP-Neural2-B",
    ]
    assert [voice["label"] for voice in voices] == [
        "学習向け（女性）Aoede",
        "ビジネス向け（男性）Orus",
        "標準（開発用）Neural2",
    ]
    assert voices[0]["tier"] == "high"
    assert voices[1]["gender"] == "male"
    assert voices[2]["tier"] == "standard"


def test_filter_sokqa_voices_omits_missing_adopted_voices_without_error() -> None:
    voices = filter_sokqa_voices(
        [
            {
                "name": "ja-JP-Chirp3-HD-Charon",
                "languageCodes": ["ja-JP"],
                "ssmlGender": "MALE",
                "naturalSampleRateHertz": 24000,
            }
        ]
    )

    assert [voice["name"] for voice in voices] == ["ja-JP-Chirp3-HD-Charon"]
    assert voices[0]["label"] == "朗読向け（男性）Charon"
