"""cnt_690bc5b8e3 で確認した18件の品質修正回帰フィクスチャ。

Text候補は learner-facing text を無人変更しないこと、TTS候補は表示本文と
正規化一致するタグ修正だけが自動適用可能であることを検証する。
"""

import pytest

from app.schemas.quality import QualityLocation
from app.services.quality_fixer import _is_safe_auto_tts_repair, apply_auto_quality_fixes


TEXT_CASES = [
    ("doc_05_doc_25", "予約名を聞かれることが多いので、次の表現も覚えておきましょう。予約していない場合は、正直に伝えてください。"),
    ("doc_06_doc_10", "I have a sharp pain in my lower back. は腰の痛みを伝える表現です。"),
    ("doc_06_doc_28", "Can I use your phone to call my family? は丁寧な依頼です。"),
    ("quiz_range_01_q_1", "connecting flights を付け加えると、意図がより明確に伝わります。"),
]


@pytest.mark.parametrize(("case_id", "text"), TEXT_CASES)
def test_18_case_text_candidates_are_not_auto_rewritten(case_id: str, text: str) -> None:
    """Text 4件は検出と修正候補を分け、生成中に自動適用しない。"""
    content = {"type": "document", "documents": [{"id": case_id, "text": text}]}
    updated, applied = apply_auto_quality_fixes(content, f"{case_id}.json")

    assert applied == []
    assert updated == content


TTS_TAG_CASES = [
    ("doc_14", "I've lost my wallet.", "[en-US]I've lost my wallet."),
    ("doc_28", "Wi-Fi環境", "[en-US]Wi-Fi[ja-JP]環境"),
    ("doc_37", "Here are my purchases and receipts.", "[en-US]Here are my purchases and receipts."),
    ("q_1", "transfer desk", "[en-US]transfer desk"),
    ("q_4_immigration", "Immigration", "[en-US]Immigration"),
    ("q_4_baggage", "Baggage Claim", "[en-US]Baggage Claim"),
    ("q_4_exit", "Exit", "[en-US]Exit"),
    ("q_4_restrooms", "Restrooms", "[en-US]Restrooms"),
    ("q_19_first", "入国審査、Passport Control", "入国審査、[en-US]Passport Control"),
    ("q_19_second", "Passport Control", "[en-US]Passport Control"),
    ("q_7_choice_1", "How long will you stay? と What is your length of stay?", "[en-US]How long will you stay?[ja-JP] と [en-US]What is your length of stay?"),
    ("q_7_choice_2", "Will you stay for how many nights? と Your nights?", "[en-US]Will you stay for how many nights?[ja-JP] と [en-US]Your nights?"),
    ("q_7_choice_3", "How many nights will you be staying? と For how many nights?", "[en-US]How many nights will you be staying?[ja-JP] と [en-US]For how many nights?"),
    ("q_7_choice_4", "How much time will you stay? と For how long?", "[en-US]How much time will you stay?[ja-JP] と [en-US]For how long?"),
]


@pytest.mark.parametrize(("case_id", "display_text", "tts_text"), TTS_TAG_CASES)
def test_18_case_tts_candidates_require_safe_tag_boundaries(
    case_id: str, display_text: str, tts_text: str
) -> None:
    """TTS 14件は、タグ除去後も表示本文と一致する場合だけ自動適用候補にできる。"""
    content = {
        "type": "document",
        "language": "ja",
        "learningLanguage": "en",
        "documents": [{"id": case_id, "text": display_text, "tts": {"text": display_text}}],
    }
    location = QualityLocation(fileName="case.json", unitId=case_id, field="text")

    assert _is_safe_auto_tts_repair(content, location, display_text, tts_text)

