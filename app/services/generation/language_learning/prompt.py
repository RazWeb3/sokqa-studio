"""Language Learning 専用 Prompt 処理（Phase 3: Prompt 移設）。

prompts.py から切り出した語学専用ロジックをここへ集約する。
系統B（日本語学習: japanese_learning 構造ポリシー / 難易度ガイダンス / 判定）を担当。
Standard 側は prompts.py に残り、Phase 3 時点は既存挙動を維持する。
"""

from app.schemas.sokqa import CoursePlan


def build_language_learning_purpose_lines(plan: CoursePlan) -> list[str]:
    """語学教材専用の生成方針（目的文）を返す純粋関数（prompts._compose_generation_purpose の語学ブロック移設）。

    専用ポリシーを持たない多言語学習パック（learningLanguage があり、かつ packLanguage と異なり、
    japanese_learning 以外）にのみ適用する指示を構築して list[str] で返す。
    副作用は持たず、呼び出し側（prompts）で purpose_lines へ結合する。
    """
    pack_language = (plan.language or "").strip()
    learning_language = (plan.learningLanguage or "").strip()
    if (
        learning_language
        and pack_language
        and learning_language != pack_language
        and plan.structurePolicy != "japanese_learning"
    ):
        return [
            "learningLanguage(学習対象言語)の語句・フレーズ・例文など、学習対象言語そのものを本文の主役として十分な分量で提示すること。パック言語は、その意味・使う場面・ニュアンスを補助的に説明する役割に用いること。学習対象言語に触れさせず、パック言語だけで学習法や概念を語る解説に終始してはならない。学習対象言語のフレーズは素のテキストとして書き、言語タグ([en-US] 等)は本文に含めないこと。タグ付けは後続の読み上げ最適化ステップの責務である。"
        ]
    return []


def is_japanese_learning_plan(plan: CoursePlan) -> bool:
    parts = [
        plan.title,
        plan.description,
        plan.shortTitle or "",
        plan.targetUser,
        *[document.title for document in plan.documents],
        *[document.goal for document in plan.documents],
        *[point for document in plan.documents for point in document.keyPoints],
    ]
    text = " ".join(str(part) for part in parts if part).lower()
    markers = ["日本語", "にほんご", "japanese", "jlpt", "n5", "n4", "ひらがな", "カタカナ"]
    return plan.language != "ja" and any(marker in text for marker in markers)


def japanese_learning_difficulty_block(plan: CoursePlan) -> str:
    if not is_japanese_learning_plan(plan):
        return ""
    if plan.difficulty == "beginner":
        guidance = """
- For learner-facing Japanese examples and target-language spans, avoid kanji in principle.
- Prefer hiragana and katakana, and use only JLPT N5-level vocabulary.
- Examples: use じこしょうかい instead of 自己紹介, あいさつ instead of 挨拶, and はじめて あう instead of 初対面.
""".rstrip()
    elif plan.difficulty == "advanced":
        guidance = """
- Learner-facing Japanese examples may use natural Japanese without kanji restrictions.
- Keep the surrounding explanation in the pack language unless a Japanese span is intentionally shown as learning content.
""".rstrip()
    else:
        guidance = """
- Learner-facing Japanese examples may use kanji up to roughly JLPT N4 level.
- Add readings or simpler phrasing when needed for accessibility.
""".rstrip()
    return f"""
Japanese-learning difficulty guidance:
{guidance}
""".rstrip()


def structure_policy_block(plan: CoursePlan) -> str:
    """japanese_learning 構造ポリシーのブロック（prompts._structure_policy_block の該当分岐）。"""
    return """
Structure policy: japanese_learning
- Write content suitable for Japanese study, keeping explanations clear and learner-friendly.
- Prefer common vocabulary and straightforward sentence structures.
""".rstrip()


def ruby_policy_block() -> str:
    """japanese_learning 用 ruby ポリシーブロック（prompts._ruby_policy_block の該当分岐）。

    呼び出し側（_ruby_policy_block）が language 引数なしで呼ぶため、ここでは固定文字列を返す。
    """
    common = """
- Parentheses used for meaning explanations are allowed and must be preserved (example: SQL（データベース操作言語）).
- Do not delete meaning/explanation parentheses just because they use （） or ().
""".strip()
    return f"""
- Ruby policy: japanese_learning
- Add furigana to every kanji word by default.
- Use reading parentheticals only for kana readings of kanji, using either Kanji(かな) or 漢字（かな）.
- Do not use reading parentheticals for meaning explanations; meaning explanations must remain as normal parentheses explanations (example: SQL（データベース操作言語）).
- When adding furigana, never repeat the same reading twice (avoid outputs that would be read as "よみ よみ").
{common}
""".strip()
