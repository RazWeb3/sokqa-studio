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

    Phase 10: 早出し構成ルール（短い導入→即・学習言語フレーズ→短い解説）は会話・フレーズ習得型の
    教材目的にのみ妥当するため、structurePolicy でゲートする。listening / summary のみ適用し、
    reading（読解型）や言語比較型等の教材目的では適用しない。summary を含める理由は、structurePolicy
    未指定時のフォールバックが summary として運用されているため。
    """
    pack_language = (plan.language or "").strip()
    learning_language = (plan.learningLanguage or "").strip()
    if (
        learning_language
        and pack_language
        and learning_language != pack_language
        and plan.structurePolicy != "japanese_learning"
        and plan.structurePolicy in {"listening", "summary"}
    ):
        return [
            "learningLanguage(学習対象言語)の語句・フレーズ・例文など、学習対象言語そのものを本文の主役として十分な分量で提示すること。パック言語は、その意味・使う場面・ニュアンスを補助的に説明する役割に用いること。学習対象言語に触れさせず、パック言語だけで学習法や概念を語る解説に終始してはならない。重要: 学習言語フレーズは必ず**言語タグなしの素のテキスト**で書くこと。本文(text)に [en-US] や [ja-JP] などの言語タグを絶対に含めてはならない。タグ付けは後続の読み上げ最適化ステップの責務であり、本文生成時は行ってはならない。",
            "各 documents[] セクションは「短い導入(1文) → 即・学習言語のフレーズをタグ無しの素のテキストで主役として提示 → 短い解説(意味・場面・ニュアンス)」の構成で書くこと。長い日本語メタ説明から始めて第1フレーズまで2〜3段落を消費する構成は禁止する。導入は1文で済ませ、最初の学習言語フレーズをセクションの早い位置(最低でも第1段落内)に置くこと。フレーズを目立たせるために言語タグで囲むことは禁止する。また、学習言語フレーズは引用符(\" や「」)で囲まず、そのままの形で本文に書くこと。",
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


def quiz_difficulty_block(plan: CoursePlan) -> str:
    """語学教材（系統A: 外国語学習）の難易度別設問深化ブロック。

    Phase 10: 共通層 quiz_generation_prompt は difficulty を「深さ」のみ定義する。
    本関数はその「深さ」を語学教材の設問設計へ具体化する（LL Strategy の責務）。
    - beginner: 意味理解・基本対応
    - intermediate: 場面適切性・使い分け
    - advanced: ニュアンス差・誤用修正・状況に応じた自然判断

    japanese_learning（系統B）は責務分離のため既存 japanese_learning_difficulty_block に委譲し、
    本関数は systemA（learningLanguage あり・非 japanese_learning）のみ適用する。
    """
    if is_japanese_learning_plan(plan) or plan.structurePolicy == "japanese_learning":
        return ""
    if not plan.learningLanguage:
        return ""
    difficulty = plan.difficulty or "standard"
    if difficulty == "beginner":
        guidance = """
- Difficulty (beginner): design questions at the level of meaning comprehension and basic matching.
  - Ask the meaning of a basic phrase, or match a phrase to its situation.
  - Every question must be grounded in a concrete learning-language phrase/expression shown in the source.
""".rstrip()
    elif difficulty == "advanced":
        guidance = """
- Difficulty (advanced): design questions requiring nuanced judgment, not surface recognition.
  - Ask about nuance differences between similar expressions, correction of unnatural wording, or selecting the most natural expression for a given situation.
  - Every question must be grounded in a concrete learning-language phrase/expression; do not ask about chapter explanation or material meta-information only.
""".rstrip()
    elif difficulty == "intermediate":
        guidance = """
- Difficulty (intermediate): design questions requiring situational appropriateness and choosing between similar expressions.
  - Ask to select the appropriate expression for a context, or to distinguish between similar expressions.
  - Every question must be grounded in a concrete learning-language phrase/expression shown in the source.
""".rstrip()
    else:
        guidance = """
- Difficulty (standard): design questions at the level of basic comprehension and matching of learning-language phrases.
  - Every question must be grounded in a concrete learning-language phrase/expression shown in the source.
""".rstrip()
    return f"""
Language-learning quiz difficulty guidance (phase 10):
{guidance}
""".strip()


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
