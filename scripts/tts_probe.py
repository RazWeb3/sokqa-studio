from __future__ import annotations

import argparse
import os
from pathlib import Path

from google.cloud import texttospeech


PROBE_TEXTS = [
    "企業の外部環境に該当する要素を選んでください。",
    "TCP と UDP の違いを説明します。",
    "CPU と GPU はどちらも演算を行います。",
    "金のなる木は投資の判断に使われます。",
    "AI と IT の活用が進んでいます。",
    "経済、技術、社会、政治の四つの観点があります。",
    "HTTP と HTTPS の違いに注意してください。",
    "バリューチェーンとサプライチェーンの違いを確認します。",
    "アンゾフの成長マトリクスでは市場と製品の組み合わせを考えます。",
    "ブルーオーシャン戦略では競争の少ない市場を探します。",
]


def _voices_from_args(value: str | None) -> list[str]:
    raw = value or os.getenv("GOOGLE_TTS_VOICES") or os.getenv("GOOGLE_TTS_VOICE") or "ja-JP-Neural2-B"
    voices = [item.strip() for item in raw.split(",") if item.strip()]
    return voices or ["ja-JP-Neural2-B"]


def _safe_voice_name(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in value)


def synthesize_text(
    client: texttospeech.TextToSpeechClient,
    *,
    text: str,
    output_path: Path,
    language_code: str,
    voice_name: str,
    speaking_rate: float,
    pitch: float,
) -> None:
    synthesis_input = texttospeech.SynthesisInput(text=text)
    voice = texttospeech.VoiceSelectionParams(
        language_code=language_code,
        name=voice_name,
    )
    audio_config = texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.MP3,
        speaking_rate=speaking_rate,
        pitch=pitch,
    )
    response = client.synthesize_speech(
        input=synthesis_input,
        voice=voice,
        audio_config=audio_config,
    )
    output_path.write_bytes(response.audio_content)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate small Google Cloud TTS mp3 probes.")
    parser.add_argument(
        "--voices",
        help="Comma-separated voice names. Defaults to GOOGLE_TTS_VOICES, GOOGLE_TTS_VOICE, or ja-JP-Neural2-B.",
    )
    parser.add_argument("--language-code", default=os.getenv("GOOGLE_TTS_LANGUAGE_CODE", "ja-JP"))
    parser.add_argument("--speaking-rate", type=float, default=float(os.getenv("GOOGLE_TTS_SPEAKING_RATE", "1.0")))
    parser.add_argument("--pitch", type=float, default=float(os.getenv("GOOGLE_TTS_PITCH", "0.0")))
    parser.add_argument("--output-dir", default=os.getenv("GOOGLE_TTS_PROBE_DIR", "tmp/tts_probe"))
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    client = texttospeech.TextToSpeechClient()
    voices = _voices_from_args(args.voices)
    sequence = 1

    for voice_name in voices:
        safe_voice = _safe_voice_name(voice_name)
        for text in PROBE_TEXTS:
            output_path = output_dir / f"{sequence:02d}_{safe_voice}.mp3"
            synthesize_text(
                client,
                text=text,
                output_path=output_path,
                language_code=args.language_code,
                voice_name=voice_name,
                speaking_rate=args.speaking_rate,
                pitch=args.pitch,
            )
            print(f"{sequence:02d}\t{voice_name}\t{text}\t{output_path}")
            sequence += 1


if __name__ == "__main__":
    main()
