"""Language Learning 専用 Prompt 処理。

prompts.py から切り出した語学専用ロジックをここへ集約する。
系統B（日本語学習: japanese_learning 構造ポリシー / 難易度ガイダンス / 判定）を担当。
Standard 側は prompts.py に残り、Phase 3 時点は既存挙動を維持する。
"""

import json

from app.schemas.sokqa import CoursePlan, PlanDocument, PlanQuizPack, SokqaDocumentPack
from app.services.pack_ids import document_pack_id, quiz_pack_id
from app.services.source_material import source_prompt_block
from app.services.tagging import document_global_tags, quiz_global_tags


def language_learning_document_generation_prompt(plan: CoursePlan, document: PlanDocument) -> str:
    """Build Language Learning documents without Standard teaching policy."""
    pack_language = plan.language
    learning_language = plan.learningLanguage or "not specified"
    root_id = document_pack_id(plan, document)
    global_tags = json.dumps(document_global_tags(plan, document), ensure_ascii=False)
    source_block = source_prompt_block(plan.sourceText, plan.sourceMode)
    source_section = f"\n\n{source_block}" if source_block else ""
    custom_instructions = (plan.customInstructions or "").strip()
    custom_block = f"\nUser-provided constraints:\n{custom_instructions}" if custom_instructions else ""
    return f"""Create one Language Learning Sokqa document JSON.

# Language Learning document design
- This is a practical language-acquisition lesson, not a general explanation of the theme.
- Each substantive section teaches one or more concrete {learning_language} phrases, dialogue turns, substitutions, or language-use distinctions.
- Present the learning-language phrase in the first sentence or immediately after one short scene-setting sentence. Do not lead with a long pack-language explanation.
- Use the pack language ({pack_language}) to explain meaning, situation, register, nuance, substitutions, and likely responses.
- Keep concrete names and locations when they make an example useful; explain that they can be replaced with the learner's own detail.
- Prefer additional usable language content—substitutions, polite alternatives, natural replies, or misuse notes—over generic summaries or travel advice.
- Do not put language tags such as [en-US] or [ja-JP] in document text. TTS is generated later.

# Output contract
- Return strict JSON only. Do not output Markdown, commentary, tts fields, or item tags.
- type must be "document" and schemaVersion must be 1.
- id must be "{root_id}"; title must be "{document.title}".
- language must be "{pack_language}" and learningLanguage must be "{learning_language}".
- Create exactly {document.targetSectionCount} documents items with ids doc-1, doc-2, and so on.
- Each item has a non-empty text field only.
{custom_block}

Document plan:
- id: {document.id}
- title: {document.title}
- goal: {document.goal}
- key points: {", ".join(document.keyPoints)}

Required JSON shape:
{{
  "id": "{root_id}",
  "type": "document",
  "schemaVersion": 1,
  "title": "{document.title}",
  "description": "{document.goal}",
  "language": "{pack_language}",
  "learningLanguage": {json.dumps(plan.learningLanguage, ensure_ascii=False)},
  "author": {json.dumps(plan.author, ensure_ascii=False)},
  "globalTags": {global_tags},
  "documents": [
    {{"id": "doc-1", "text": "Concrete learner-facing language lesson text."}}
  ]
}}
{source_section}
""".strip()


def language_learning_quiz_generation_prompt(
    plan: CoursePlan,
    quiz_pack: PlanQuizPack,
    source_documents: list[SokqaDocumentPack],
) -> str:
    """Build the complete Language Learning quiz prompt without Standard quiz policy.

    The Standard prompt is intentionally not composed into this function.
    Only transport/schema requirements are shared through imported utilities;
    teaching design, question forms, language contracts, and source grounding
    are owned here.
    """
    learning_language = plan.learningLanguage or "not specified"
    pack_language = plan.language
    source_text = "\n\n".join(
        f"## {document.title}\n" + "\n".join(item.text for item in document.documents)
        for document in source_documents
    ).strip()
    if not source_text:
        source_text = (plan.sourceText or "").strip()
    if not source_text:
        source_text = "No source text is available; create no factual or general-knowledge questions."

    if quiz_pack.choiceLanguageMode == "pack":
        mode_contract = f"""
# pack mode contract
- A question presents one or more concrete {learning_language} phrases, dialogue turns, or language-use distinctions from the source.
- All four choices are written in the pack language ({pack_language}). They describe the meaning, situation, politeness, naturalness, correction, or nuance of the presented learning-language content.
- Do not use four learning-language answer candidates in this mode. A question such as "which English expression is best?" belongs to learning mode.
- The question and explanation may quote learning-language phrases. Choices remain pack-language explanations.
""".strip()
    elif quiz_pack.choiceLanguageMode == "learning":
        mode_contract = f"""
# learning mode contract
- A question presents a scenario, intent, or contrast in the pack language ({pack_language}) and is anchored in source expressions.
- All four choices are written in the learning language ({learning_language}); they are plausible alternative expressions, replies, or corrections.
- Do not use pack-language-only general-knowledge, etiquette, procedure, or travel-manner choices.
""".strip()
    else:
        mode_contract = f"""
# auto mode contract
- For each question, choose either the pack-mode structure or the learning-mode structure based on the source phrase and tested skill.
- Keep all four choices in one language within each question. Never mix pack-language and learning-language choices.
""".strip()

    difficulty = quiz_difficulty_block(plan)
    custom_instructions = (plan.customInstructions or "").strip()
    custom_block = f"\nUser-provided constraints:\n{custom_instructions}" if custom_instructions else ""
    root_id = quiz_pack_id(plan, quiz_pack)
    global_tags = json.dumps(quiz_global_tags(plan, quiz_pack), ensure_ascii=False)
    return f"""Create one Language Learning Sokqa quiz JSON.

# Language Learning question design
- This is a language-acquisition quiz, not a travel, safety, etiquette, or general-knowledge quiz.
- Before writing each question, select a concrete source phrase, source situation, and tested skill internally. Do not output these planning fields.
- Every correct answer must be supported by the source content. Distractors may be new, but must be plausible language alternatives or plausible interpretations of the source phrase.
- Reuse source expressions across a pack only when testing a distinct skill; do not pad the requested count with theme facts or generic advice.
- Use varied skills where the source supports them: meaning, situation appropriateness, naturalness, politeness/register, dialogue response, misuse correction, and nuance.
- The explanation is concise, uses the pack language, and explains why the selected answer is correct.

{mode_contract}

{difficulty}

# Output contract
- Return strict JSON only. Do not output Markdown, commentary, or tts fields.
- type must be "quiz" and schemaVersion must be 1.
- id must be "{root_id}"; title must be "{quiz_pack.title}".
- language must be "{pack_language}" and learningLanguage must be "{learning_language}".
- choiceLanguageMode must be "{quiz_pack.choiceLanguageMode}" and must not be changed.
- description is a short pack-language description.
- Output exactly {quiz_pack.questionCount} questions. Each has an id, a finished question, exactly four non-empty string choices, an integer answerIndex from 0 to 3, and a specific explanation.
- The selected answer must be the only correct answer, and the explanation must support that exact answer.
{custom_block}

Quiz pack:
- id: {quiz_pack.id}
- title: {quiz_pack.title}
- purpose: {quiz_pack.purpose}

Source documents:
{source_text}

Required JSON shape:
{{
  "id": "{root_id}",
  "type": "quiz",
  "schemaVersion": 1,
  "title": "{quiz_pack.title}",
  "description": "Short quiz description in {pack_language}.",
  "language": "{pack_language}",
  "learningLanguage": {json.dumps(plan.learningLanguage, ensure_ascii=False)},
  "choiceLanguageMode": "{quiz_pack.choiceLanguageMode}",
  "author": {json.dumps(plan.author, ensure_ascii=False)},
  "globalTags": {global_tags},
  "questions": [
    {{
      "id": "q-1",
      "question": "Finished learner-facing question.",
      "choices": ["choice 1", "choice 2", "choice 3", "choice 4"],
      "answerIndex": 0,
      "explanation": "Specific explanation for choice 1."
    }}
  ]
}}
""".strip()


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
            "会話・実用表現型の各 documents[] セクションは、「短い導入」(学習場面を短く提示)から始め、(2)中心フレーズをすぐタグ無しで提示、(3)パック言語で意味と使用場面を説明、(4)相手の発話と学習者の返答、(5)自然な言い換え・類似表現、(6)聞き返し・確認・代替案・交渉などの発展例、(7)短い振り返り、の順で教えること。After a short introduction, present the learning-language phrase immediately. Avoid long explanations before introducing the first learning-language phrase. Keep explanations concise: prefer Phrase → Meaning → Usage instead of long paragraphs. beginner は一場面一表現と短く明確な応答、standard は類似表現の比較、場面に応じた表現選択、より自然な言い回し、適切な返答、advanced は丁寧さ、自然な言い換え、曖昧な依頼の具体化、提案確認、交渉、誤用修正、文脈ニュアンスを扱う。長い日本語メタ説明から始めて第1フレーズまで2〜3段落を消費する構成は禁止する。導入は1文で済ませ、最初の学習言語フレーズを第1段落内に置くこと。フレーズを目立たせるために言語タグで囲むことは禁止する。引用符(\" や「」)で囲まず、そのまま本文に書くこと。",
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
    - standard: 類似表現比較・場面適切性・使い分け
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
  - Prefer nuance differences, correction of clear misuse, formal versus casual register, naturalness judgment, or selecting the optimal expression for a given situation.
  - Do not let the quiz pack become dominated by simple meaning-comprehension questions. Meaning questions remain allowed when they support the learning goal.
  - Every question must be grounded in a concrete learning-language phrase/expression; do not ask about chapter explanation or material meta-information only.
""".rstrip()
    elif difficulty == "standard":
        guidance = """
- Difficulty (standard): design questions requiring situational appropriateness and choosing between similar expressions.
  - Ask to compare similar expressions, select an expression for the situation, choose a more natural wording, or select an appropriate reply.
  - Every question must be grounded in a concrete learning-language phrase/expression shown in the source.
""".rstrip()
    else:
        guidance = """
- Difficulty (fallback): design questions at the level of basic comprehension and matching of learning-language phrases.
  - Every question must be grounded in a concrete learning-language phrase/expression shown in the source.
""".rstrip()
    return f"""
Language-learning quiz difficulty guidance (phase 10):
{guidance}
- Across a quiz pack, vary question forms where the source material and question count allow it. Mix meaning, situation appropriateness, naturalness, politeness/register, dialogue response, misuse correction, and nuance instead of repeating one form excessively. Do not require every form when the pack is short or the source does not support it.
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
