from app.schemas.sokqa import GeneratedFile, SokqaQuestion, SokqaQuizPack
from app.services.tts_optimizer import validate_tts_files
from app.services.tts_sentence_policy import find_long_sentences


Q25_EXPLANATION = (
    "集中豪雨による浸水リスクに対しては、従業員の安全確保と事業継続のため、"
    "在宅勤務体制の確立や高台の代替オフィス準備など、交通網の寸断や建物への被害を"
    "想定した対策を優先的に考慮します。"
)


def test_find_long_sentences_reports_only_the_oversized_sentence() -> None:
    issues = find_long_sentences("短い文です。" + Q25_EXPLANATION)

    assert issues == [Q25_EXPLANATION]


def test_tts_report_warns_about_a_long_raw_sentence_before_recording() -> None:
    pack = SokqaQuizPack(
        id="quiz-long-sentence",
        title="長文テスト",
        questions=[
            SokqaQuestion(
                id="q-1",
                question="質問ですか？",
                choices=["はい", "いいえ", "どちらでもない", "不明"],
                answerIndex=0,
                explanation=Q25_EXPLANATION,
            )
        ],
    )
    file = GeneratedFile(name="quiz.json", kind="quiz", content=pack.model_dump(exclude_none=True))

    report = validate_tts_files([file], mode="rule")

    assert [(issue.itemId, issue.field, issue.issueType) for issue in report.issues] == [
        ("q-1", "explanationText", "sentence_too_long")
    ]
