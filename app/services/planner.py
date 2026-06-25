import hashlib
import re
from datetime import date
from typing import Any

from app.config import get_settings
from app.schemas.common import ReadingPattern, normalize_tts_reading_mode, source_mode_for_material_mode
from app.schemas.request import PlanPackRequest, QuizPackSpec
from app.schemas.sokqa import CoursePlan, PlanDocument, PlanQuizPack
from app.services.gemini_client import GeminiClient
from app.services.document_generator import (
    STRICT_MAX_DOCUMENT_FILES,
    split_strict_source_sections,
    strict_source_limit_error,
    strict_source_paragraphs,
)
from app.services.llm_json import LlmJsonParseContext
from app.services.pack_metadata import resolve_creator_id
from app.services.source_material import normalize_source, source_prompt_block
from app.services.tagging import course_global_tags
from app.utils.ids import new_opaque_id, path_token, slugify


SCALE_CHAPTER_RANGES = {
    "quick": (3, 3),
    "standard": (6, 6),
    "large": (9, 9),
    "auto": (8, 12),
}

RANGE_TITLES = {
    2: ["前半の理解チェック", "後半の理解チェック"],
    3: ["前半の理解チェック", "中盤の理解チェック", "後半の理解チェック"],
    4: ["序盤の理解チェック", "前半の理解チェック", "後半の理解チェック", "終盤の理解チェック"],
}

RANGE_PURPOSES = ["key_concepts", "application", "application", "application"]
SECTION_COUNT_MIN = 35
SECTION_COUNT_MAX = 50


# ── 読み候補(proposedReadingPatterns)の生成方針 ──────────────
# 読み候補は次の3段構成で決まる。役割が異なるので混同しないこと。
#  (1) カテゴリ判定: theme + customInstructions を主体に
#      6カテゴリ(技術/文学/資格/ビジネス/語学/汎用)へマッチさせ、
#      該当カテゴリのテンプレートだけを候補に入れる。
#      sourceText 本文は技術判定に使わない(誤爆防止のため)。
#  (2) 重複統合: _reading_pattern_signature で意味的に同種の候補
#      (LLM由来とfallback由来など)を1つにまとめる。
#  (3) recommended補正: 候補が属するカテゴリごとの固定ルールで
#      初期チェック(recommended=true)を上書きする。
#      sourceText / documents は recommended 判定に使わない。
#      ※(1)は「候補を出すか」、(3)は「初期選択にするか」で層が違う。
# ────────────────────────────────────────────────
FALLBACK_READING_PATTERNS_BY_CATEGORY = {
    "technical": [
        ReadingPattern(
            id="alphabet_abbreviations",
            title="英略語はアルファベット読みで扱う",
            description="IT、API、CLI などの英略語は、必要に応じてカタカナのアルファベット読みとして扱う方針です。",
            examples=["IT -> アイティー", "API -> エーピーアイ", "CLI -> シーエルアイ"],
            recommended=True,
        ),
        ReadingPattern(
            id="dot_notation",
            title="ドット記法やファイル名を読み下す",
            description=".git、.env、.gitignore、app.config のようなドットや記号を含む表記を、読み上げで自然に聞こえるように扱う方針です。",
            examples=[".git -> ドット ギット", ".env -> ドット イーエヌブイ", ".gitignore -> ドット ギットイグノア"],
            recommended=True,
        ),
        ReadingPattern(
            id="technical_commands",
            title="コマンドや技術用語を読み下す",
            description="git checkout や npm install のようなコマンド・技術用語を、聞き取りやすい読みとして扱う方針です。",
            examples=["git checkout -> ギット チェックアウト", "npm install -> エヌピーエム インストール"],
            recommended=False,
        ),
        ReadingPattern(
            id="camel_case_terms",
            title="キャメルケースや区切り語を読みやすくする",
            description="localStorage や accessToken のような区切りのある技術語を、自然なまとまりで読めるように扱う方針です。",
            examples=["localStorage -> ローカルストレージ", "accessToken -> アクセストークン"],
            recommended=False,
        ),
        ReadingPattern(
            id="symbols_and_versions",
            title="記号・バージョン番号を聞き取りやすくする",
            description="スラッシュ、ハイフン、バージョン番号などを、聞き取りやすい読みとして扱う方針です。",
            examples=["v1.2 -> バージョン いち てん に", "A/B -> エー スラッシュ ビー"],
            recommended=False,
        ),
    ],
    "literature": [
        ReadingPattern(
            id="literary_difficult_words",
            title="難読語や文学語彙に読みを付ける",
            description="歌詞、詩、小説、古典などで難読語や文学的な語彙を、聞き取りやすい読みとして扱う方針です。",
            examples=["黄昏 -> たそがれ", "静寂 -> しじま"],
            recommended=True,
        ),
        ReadingPattern(
            id="literary_proper_nouns",
            title="作品名・人物名・地名の固有名詞を読み下す",
            description="作品世界の固有名詞や作者名などを、一定の読みで扱う方針です。",
            examples=["芥川龍之介 -> あくたがわ りゅうのすけ", "百人一首 -> ひゃくにんいっしゅ"],
            recommended=False,
        ),
    ],
    "qualification": [
        ReadingPattern(
            id="exam_official_names",
            title="試験名や制度名を正式名称で読み下す",
            description="資格試験や制度の正式名称を、省略しすぎず安定した読みとして扱う方針です。",
            examples=["基本情報技術者試験 -> きほんじょうほうぎじゅつしゃしけん", "日商簿記2級 -> にっしょう ぼき にきゅう"],
            recommended=True,
        ),
        ReadingPattern(
            id="exam_abbreviations",
            title="試験で頻出の略語を読み下す",
            description="資格分野で繰り返し出る略語や区分表記を、聞き取りやすい読みとして扱う方針です。",
            examples=["FP -> エフピー", "TOEIC -> トーイック"],
            recommended=False,
        ),
    ],
    "business": [
        ReadingPattern(
            id="business_roles_departments",
            title="部署名・役職名・社内用語を読み下す",
            description="部署名、役職名、社内で使う定型語を、聞き取りやすい読みとして扱う方針です。",
            examples=["経営企画部 -> けいえいきかくぶ", "執行役員 -> しっこうやくいん"],
            recommended=True,
        ),
        ReadingPattern(
            id="business_abbreviations",
            title="ビジネス略語をカタカナで読み下す",
            description="KPI、ROI、B2B などのビジネス略語を、会議や研修で聞き取りやすい読みとして扱う方針です。",
            examples=["KPI -> ケーピーアイ", "ROI -> アールオーアイ"],
            recommended=False,
        ),
    ],
    "language": [
        ReadingPattern(
            id="language_kanji_readings",
            title="漢字語彙や難読語に読みを付ける",
            description="日本語学習や国語教材で、漢字語彙や難読語を聞き取りやすい読みとして扱う方針です。",
            examples=["語彙 -> ごい", "敬語 -> けいご"],
            recommended=True,
        ),
        ReadingPattern(
            id="language_example_readings",
            title="学習語彙や例文の読みを安定させる",
            description="例文に出る学習語彙や表記ゆれしやすい語を、一定の読みとして扱う方針です。",
            examples=["一昨日 -> おととい", "相槌 -> あいづち"],
            recommended=False,
        ),
    ],
    "generic": [
        ReadingPattern(
            id="generic_numbers_and_symbols",
            title="数字・記号・区切りを聞き取りやすくする",
            description="番号、スラッシュ、ハイフンなど、一般テーマでも読みがぶれやすい表記を整理する方針です。",
            examples=["第3章 -> だいさんしょう", "A/Bテスト -> エー スラッシュ ビー テスト"],
            recommended=True,
        ),
        ReadingPattern(
            id="generic_proper_names",
            title="固有名詞の読みを一定にする",
            description="人名、地名、ブランド名などの固有名詞を一定の読みで扱う方針です。",
            examples=["御茶ノ水 -> おちゃのみず", "重慶 -> じゅうけい"],
            recommended=False,
        ),
    ],
}

CATEGORY_KEYWORDS = {
    "technical": (
        "git",
        "github",
        "npm",
        "localStorage",
        ".git",
        ".env",
        ".gitignore",
        "API",
        "CLI",
        "JSON",
        "YAML",
        "JavaScript",
        "TypeScript",
        "Node.js",
        "Docker",
        "Kubernetes",
        "ITパスポート",
        "基本情報技術者",
        "応用情報",
        "プログラミング",
        "ソフトウェア",
        "Web開発",
        "コマンド",
        "ファイル名",
        "環境変数",
        "技術書",
        "技術",
    ),
    "literature": (
        "歌詞",
        "詩",
        "短歌",
        "俳句",
        "文学",
        "小説",
        "古文",
        "現代文",
        "評論",
        "随筆",
        "読解",
        "作品",
    ),
    "qualification": (
        "資格",
        "検定",
        "試験対策",
        "模擬試験",
        "過去問",
        "簿記",
        "TOEIC",
        "TOEFL",
        "英検",
        "宅建",
        "社労士",
        "中小企業診断士",
        "ITパスポート",
        "基本情報技術者",
        "JLPT",
    ),
    "business": (
        "ビジネス",
        "経営",
        "営業",
        "人事",
        "採用",
        "マーケティング",
        "財務",
        "経理",
        "会議",
        "社内",
        "マネジメント",
        "組織",
        "部署",
        "役職",
        "KPI",
        "ROI",
        "OKR",
        "B2B",
    ),
    "language": (
        "日本語学習",
        "英語学習",
        "英会話",
        "語学",
        "国語",
        "漢字",
        "語彙",
        "文法",
        "JLPT",
        "N1",
        "N2",
        "N3",
        "N4",
        "N5",
        "日本語",
        "英語",
    ),
    "generic": (
        "読み方",
        "音読",
        "朗読",
        "ナレーション",
        "アナウンス",
        "スピーチ",
        "発声",
    ),
}

CATEGORY_ORDER = ("technical", "literature", "qualification", "business", "language", "generic")
FALLBACK_PATTERN_CATEGORY_BY_ID = {
    pattern.id: category
    for category, patterns in FALLBACK_READING_PATTERNS_BY_CATEGORY.items()
    for pattern in patterns
}
CATEGORY_RECOMMENDED_SIGNATURES = {
    "technical": {"alphabet_abbreviations"},
    "literature": {"literary_difficult_words"},
    "qualification": {"exam_official_names"},
    "business": {"business_roles_departments"},
    "language": {"language_kanji_readings"},
    "generic": {"generic_numbers_and_symbols"},
}


def _language_base(language: str | None) -> str:
    return (language or "ja").split("-")[0].lower()


def _compile_category_keyword(keyword: str) -> re.Pattern[str]:
    if keyword.startswith("."):
        return re.compile(rf"(?<![A-Za-z0-9_]){re.escape(keyword)}(?![A-Za-z0-9_])", re.IGNORECASE)
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.\-_/ ]*", keyword):
        escaped = re.escape(keyword).replace(r"\ ", r"\s+")
        return re.compile(rf"(?<![A-Za-z0-9_]){escaped}(?![A-Za-z0-9_])", re.IGNORECASE)
    return re.compile(re.escape(keyword), re.IGNORECASE)


CATEGORY_KEYWORD_PATTERNS = {
    category: tuple(_compile_category_keyword(keyword) for keyword in keywords)
    for category, keywords in CATEGORY_KEYWORDS.items()
}


def _is_ja(language: str | None) -> bool:
    return _language_base(language) == "ja"


def _localized_label(language: str | None, key: str) -> str:
    labels = {
        "ja": {
            "learning_pack": "学習パック",
            "generated_on": "生成日",
            "comprehension_check": "理解チェック",
            "integrated_review": "総合確認",
            "full_range": "全範囲",
            "chapter": "章",
            "chapters": "章",
        },
        "en": {
            "learning_pack": "Learning Pack",
            "generated_on": "Generated on",
            "comprehension_check": "Comprehension Check",
            "integrated_review": "Integrated Review",
            "full_range": "Full Range",
            "chapter": "Chapter",
            "chapters": "Chapters",
        },
        "ko": {
            "learning_pack": "학습 팩",
            "generated_on": "생성일",
            "comprehension_check": "이해 확인",
            "integrated_review": "종합 복습",
            "full_range": "전체 범위",
            "chapter": "장",
            "chapters": "장",
        },
        "zh": {
            "learning_pack": "学习包",
            "generated_on": "生成日期",
            "comprehension_check": "理解检查",
            "integrated_review": "综合复习",
            "full_range": "全部范围",
            "chapter": "章",
            "chapters": "章",
        },
        "es": {
            "learning_pack": "paquete de aprendizaje",
            "generated_on": "Generado el",
            "comprehension_check": "Comprobacion de comprension",
            "integrated_review": "Repaso integral",
            "full_range": "rango completo",
            "chapter": "capitulo",
            "chapters": "capitulos",
        },
    }
    base = _language_base(language)
    return labels.get(base, labels["en"])[key]


def _ai_disclaimer(language: str | None) -> str:
    disclaimers = {
        "ja": "この内容はAIが生成したものです。重要な判断の前にご自身で事実確認をしてください。",
        "en": "This content was generated by AI. Please verify facts yourself before making important decisions.",
        "ko": "이 콘텐츠는 AI가 생성했습니다. 중요한 판단 전에 직접 사실을 확인해 주세요.",
        "zh": "本内容由 AI 生成。做出重要判断前，请自行核实事实。",
        "es": "Este contenido fue generado por IA. Verifica los datos antes de tomar decisiones importantes.",
    }
    return disclaimers.get(_language_base(language), disclaimers["en"])


def _document_count(request: PlanPackRequest) -> int:
    if request.generationUnit == "quiz":
        return 0
    if request.docCount is not None:
        return request.docCount
    if request.documentCount:
        return request.documentCount
    if request.generationUnit == "document":
        return 1
    if request.scale == "quick":
        return 3
    if request.scale == "standard":
        return 6
    if request.scale == "large":
        return 9
    return 10


def _exact_document_count(request: PlanPackRequest) -> int | None:
    if request.generationUnit == "quiz":
        return 0
    if request.docCount is not None:
        return request.docCount
    if request.documentCount:
        return request.documentCount
    if request.generationUnit == "document":
        return 1
    if request.scale == "quick":
        return 3
    if request.scale == "standard":
        return 6
    if request.scale == "large":
        return 9
    return None


def _question_count(request: PlanPackRequest) -> int:
    return request.questionCount or 30


def _quiz_pack_count(request: PlanPackRequest, document_count: int) -> int:
    if request.generationUnit == "document":
        return 0
    if request.quizCount is not None:
        return request.quizCount
    if request.quizPacks:
        return len(request.quizPacks)
    if request.generationUnit == "quiz":
        return 1
    if request.scale == "quick":
        return 1
    if request.scale == "standard":
        return 2
    if request.scale == "large":
        return 3
    if document_count <= 8:
        return 3
    if document_count <= 10:
        return 4
    return 5


def _range_quiz_pack_count(scale: str, document_count: int) -> int:
    if scale in {"quick", "standard"}:
        return 2
    if document_count <= 6:
        return 2
    if document_count <= 10:
        return 3
    return 4


def _default_choice_language_mode(learning_language: str | None) -> str:
    return "learning" if learning_language else "auto"


def _apply_requested_choice_language_modes(
    quiz_packs: list[PlanQuizPack],
    request: PlanPackRequest,
    learning_language: str | None,
) -> list[PlanQuizPack]:
    if not quiz_packs:
        return quiz_packs
    fallback = _default_choice_language_mode(learning_language)
    requested_modes = list(request.quizChoiceLanguageModes or [])
    normalized: list[PlanQuizPack] = []
    for index, quiz_pack in enumerate(quiz_packs):
        mode = requested_modes[index] if index < len(requested_modes) else quiz_pack.choiceLanguageMode or fallback
        normalized.append(quiz_pack.model_copy(update={"choiceLanguageMode": mode}))
    return normalized


def _split_document_ids(document_ids: list[str], chunk_count: int) -> list[list[str]]:
    if chunk_count <= 0:
        return []
    base_size, remainder = divmod(len(document_ids), chunk_count)
    chunks = []
    start = 0
    for index in range(chunk_count):
        size = base_size + (1 if index < remainder else 0)
        end = start + size
        chunks.append(document_ids[start:end])
        start = end
    return chunks


def _section_count(request: PlanPackRequest, index: int = 1, total_documents: int = 1) -> int:
    if request.sectionsPerDocument:
        return request.sectionsPerDocument
    span = SECTION_COUNT_MAX - SECTION_COUNT_MIN + 1
    seed_input = (
        f"{request.theme}|{request.targetUser}|{request.difficulty}|"
        f"{request.scale}|{max(1, total_documents)}"
    )
    base_offset = int(hashlib.sha256(seed_input.encode("utf-8")).hexdigest()[:8], 16) % span
    # Use a coprime stride so the first 15 documents do not collapse to one repeated value.
    spread_offset = ((max(1, index) - 1) * 5) % span
    return SECTION_COUNT_MIN + ((base_offset + spread_offset) % span)


def _requested_document_count(request: PlanPackRequest) -> str:
    if request.generationUnit == "quiz":
        return "0 chapters. The user requested quiz-only generation."
    if request.docCount is not None:
        return (
            f"{request.docCount} chapters exactly. "
            "You must return exactly this many documents."
        )
    if request.documentCount:
        return (
            f"{request.documentCount} chapters exactly. "
            "You must return exactly this many documents."
        )
    if request.scale == "auto":
        return (
            "Not specified. Scale is auto: return 8-12 chapters. "
            "Let the source complexity, theme, target user, and difficulty decide the exact count."
        )
    min_count, max_count = SCALE_CHAPTER_RANGES[request.scale]
    return (
        f"Not specified. Scale is {request.scale}: return {min_count}-{max_count} chapters. "
        "Within that range, optimize the chapter count for the theme, target user, and difficulty."
    )


def _requested_section_count(request: PlanPackRequest) -> str:
    if request.sectionsPerDocument:
        return (
            f"{request.sectionsPerDocument} sections per document exactly. "
            "Every document.targetSectionCount must use this number."
        )
    return (
        "Not specified. Determine an integer targetSectionCount from 35 to 50 for each chapter. "
        "Choose different values per chapter based on topic breadth and expected content density. "
        "Do not collapse every chapter to the same midpoint or reuse one safe default across the whole pack."
    )


def _fallback_short_title(request: PlanPackRequest) -> str:
    text = re.sub(r"\s+", "", request.theme).strip()
    text = re.sub(r"(学習パック|講座|コース)$", "", text)
    if not text:
        return "Sokqa"
    if len(text) > 16:
        return text[:16]
    return text


def _description_from_request(request: PlanPackRequest, fallback: str) -> str:
    description = fallback
    if request.descriptionMode == "manual" and request.manualDescription:
        description = request.manualDescription.strip()
    extras: list[str] = []
    if request.descriptionIncludeDate:
        label = _localized_label(request.language, "generated_on")
        extras.append(f"{label}: {date.today().isoformat()}")
    if request.descriptionIncludeAiDisclaimer:
        extras.append(_ai_disclaimer(request.language))
    if extras:
        description = f"{description}\n" + "\n".join(extras)
    return description


def _fallback_pack_title(request: PlanPackRequest) -> str:
    return f"{request.theme} {_localized_label(request.language, 'learning_pack')}"


def _fallback_pack_description(request: PlanPackRequest) -> str:
    if _is_ja(request.language):
        return f"{request.targetUser}向けの{request.theme}用Sokqa学習パックです。"
    return f"A Sokqa learning pack about {request.theme} for {request.targetUser}."


def _fallback_document_title(request: PlanPackRequest, index: int) -> str:
    return f"{request.theme}の重要領域 {index}" if _is_ja(request.language) else f"Key Area {index} of {request.theme}"


def _fallback_document_goal(request: PlanPackRequest, index: int) -> str:
    if _is_ja(request.language):
        return f"{request.targetUser}が{request.theme}の領域{index}で扱う基本事項と実践上の注意点を理解する"
    return f"{request.targetUser} will understand the fundamentals and practical cautions in area {index} of {request.theme}."


def _fallback_document_key_points(request: PlanPackRequest, index: int) -> list[str]:
    if _is_ja(request.language):
        return [
            f"{request.theme}の領域{index}で最初に押さえる用語",
            f"領域{index}で起こりやすい誤解",
            f"{request.targetUser}が実務や学習で使う場面",
        ]
    return [
        f"Core terms to learn first in area {index} of {request.theme}",
        f"Common misunderstandings in area {index}",
        f"How {request.targetUser} can use this in practice or study",
    ]


def _global_tags_from_request(request: PlanPackRequest, plan: CoursePlan) -> list[str]:
    if request.globalTagsMode == "manual" and request.manualGlobalTags:
        return request.manualGlobalTags[:3]
    return course_global_tags(plan)


def _effective_request_tts_mode(request: PlanPackRequest) -> str:
    if not request.includeTts or not request.enableTtsOptimize:
        return "none"
    return normalize_tts_reading_mode(request.ttsReadingMode) or get_settings().tts_reading_mode


def infer_learning_language(theme: str, target_user: str = "") -> str | None:
    markers = [
        ("ja", ["日本語", "にほんご", "japanese", "jlpt"]),
        ("ko", ["韓国語", "朝鮮語", "korean", "ハングル"]),
        ("zh", ["中国語", "中文", "chinese", "北京語"]),
        ("id", ["インドネシア語", "bahasa indonesia", "indonesian"]),
        ("en", ["英会話", "英語", "english"]),
        ("es", ["スペイン語", "spanish", "español"]),
        ("fr", ["フランス語", "french", "français"]),
        ("de", ["ドイツ語", "german", "deutsch"]),
        ("it", ["イタリア語", "italian", "italiano"]),
        ("pt", ["ポルトガル語", "portuguese", "português"]),
    ]
    for text in (theme.casefold(), target_user.casefold()):
        for language, candidates in markers:
            if any(marker in text for marker in candidates):
                return language
    return None


def _planner_prompt(request: PlanPackRequest) -> str:
    source_block = source_prompt_block(request.sourceText, request.sourceMode)
    source_section = f"\n\n{source_block}" if source_block else ""
    tts_mode = _effective_request_tts_mode(request)
    reading_pattern_rules = (
        """
- Also propose optional reading-pattern policies that may help TTS generation for this theme.
- proposedReadingPatterns are selectable policies, not fixed word dictionaries. Do not mix them with ttsRules.
- Each reading pattern should describe a general reading strategy, include 1 to 3 examples, and use a stable snake_case id.
- Examples must be real transformations in "source -> reading" format: source and reading must not be identical, source and reading must not be empty, and do not append stray suffixes or unrelated characters.
- Do not use already-natural katakana words as examples unless the source contains symbols, ASCII, kanji, or other notation that actually changes in the reading.
- Consider these common categories and propose the ones that are relevant to the theme:
  - dot notation and symbol-heavy file/config names, e.g. ".git -> ドットギット", ".env -> ドットイーエヌブイ", ".gitignore -> ドットギットイグノア".
  - alphabet reading for abbreviations, e.g. "OS -> オーエス", "API -> エーピーアイ", "URL -> ユーアールエル".
  - commands and technical phrases, e.g. "git checkout -> ギット チェックアウト".
  - camelCase or delimiter-separated terms, e.g. "localStorage -> ローカルストレージ".
- Prioritize categories that match the theme/source text. Do not force unrelated patterns just to fill the list.
- Judge genre primarily from theme and customInstructions. Use sourceText only as supporting evidence and never as the sole reason to add a genre-specific reading pattern.
- Do not infer technical patterns from short general words such as "it", "ai", or "os" when they appear as ordinary words.
- Do not propose command, file-name, camelCase, dot-notation, or version-number patterns unless the theme or customInstructions clearly indicate technical content.
- For lyrics, literature, business, qualification, and language-learning themes, keep the proposed patterns inside that genre and avoid unrelated technical patterns.
- Set recommended=true only when the notation is likely to appear in this theme, target user, source text, document titles, or key points. Set dot notation and command patterns to recommended=false unless dot files, commands, file names, or similar notation actually appear.
- Examples must use the concrete "source -> reading" format so users can judge the pattern quickly.
""".rstrip()
        if tts_mode == "llm"
        else """
- Do not propose reading-pattern policies because the selected TTS mode will not use them.
- Return proposedReadingPatterns as an empty array.
""".rstrip()
    )
    additional_conditions = request.customInstructions or "none"
    return f"""
Return strict JSON only. Do not use markdown fences.
出力は必ずJSONのみ。Markdown、説明文、コードブロックは禁止。JSON内の文字列は必ずエスケープする。

Design a Sokqa CoursePlan outline for a learning pack.
The user will review and edit this outline before generation, so focus on a concrete, useful chapter plan.

Input:
- theme: {request.theme}
- targetUser: {request.targetUser}
- difficulty: {request.difficulty}
- scale: {request.scale}
- language: {request.language}
- learningLanguage: {request.learningLanguage or infer_learning_language(request.theme, request.targetUser) or "not specified"}
- structurePolicy: {request.structurePolicy}
- generationUnit: {request.generationUnit}
- requested quizCount: {request.quizCount if request.quizCount is not None else "planner/default"}
- requested questionCount: {request.questionCount if request.questionCount is not None else "30 per quiz"}
- materialMode: {request.materialMode}
- requested documentCount: {_requested_document_count(request)}
- requested sectionsPerDocument: {_requested_section_count(request)}

# 生成ルール
以下は必ず守る制約です。出力本文には含めないでください。
additional conditions: {additional_conditions}
{additional_conditions}
{source_section}

Rules:
- The documents array is the most important output.
- Write title, shortTitle, description, document titles/goals/keyPoints, quiz-related labels, and automatic metadata in the pack language ({request.language}) unless the user explicitly supplied those fields in another language.
- Respect the user's additional conditions when they are provided, while still following the schema, safety, material mode, and generation-unit constraints.
- structurePolicy standard: use the existing balanced course structure.
- structurePolicy listening: write the outline as connected narrative beats for listening-first content. Do not design glossary entries or term-by-term definition lists. Prefer fewer sections that flow from context to explanation to examples, with short natural sentences and minimal symbol-heavy or bullet-list-dependent structure.
- materialMode reference: reference material may be supplemented when needed.
- materialMode source_only: use only the supplied material, but you may organize and rewrite it as learning content.
- materialMode strict: plan for source-only document generation. The final document text will be copied mechanically from the material without LLM rewriting. Do not add facts, terms, examples, claims, or inferred details that are absent from the material.
- Also return shortTitle: a short pack identifier used as a title prefix, such as "Git入門". Keep it concise.
- Chapter titles must describe the actual topic content. Do not return generic titles such as "第1章" or "{request.theme} 第1章".
- The document order must be a natural learning path from basics to application/review.
- Each document must have a unique, theme-specific title.
- Each document.goal must describe what the learner will understand in that specific chapter.
- Each document.keyPoints must be specific to that chapter. Do not reuse the same keyPoints across chapters.
- If documentCount was specified, return exactly that many documents.
- If documentCount was not specified and scale is quick, return 3-5 documents.
- If documentCount was not specified and scale is quick, return exactly 3 documents.
- If documentCount was not specified and scale is standard, return exactly 6 documents.
- If documentCount was not specified and scale is large, return exactly 9 documents.
- If documentCount was not specified and scale is auto, return 8-12 documents.
- If sectionsPerDocument was specified, every targetSectionCount must exactly match it.
- keyPoints should contain 3 to 6 concise items. For structurePolicy listening, keyPoints must be narrative beats in the order the spoken explanation should flow, not isolated term labels.
- If sectionsPerDocument was not specified, targetSectionCount must be an integer from 35 to 50 for every document.
- Decide targetSectionCount per chapter based on the breadth of the topic and expected explanation density.
- Use different targetSectionCount values across chapters when the content scope differs. Do not flatten every chapter to one safe midpoint value.
- targetSectionCount must be an integer from 1 to 50.
{reading_pattern_rules}

Return this JSON shape:
{{
  "title": "pack title",
  "shortTitle": "short pack identifier",
  "description": "short description, including why this chapter count fits if documentCount was not specified",
  "proposedReadingPatterns": [
    {{
      "id": "alphabet_abbreviations",
      "title": "short pattern title",
      "description": "what to do when generating learner text",
      "examples": ["IT -> アイティー"],
      "recommended": true
    }}
  ],
  "documents": [
    {{
      "title": "specific chapter title 1",
      "goal": "chapter-specific learning goal 1",
      "keyPoints": ["specific point 1", "specific point 2", "specific point 3"],
      "targetSectionCount": 38
    }},
    {{
      "title": "specific chapter title 2",
      "goal": "chapter-specific learning goal 2",
      "keyPoints": ["specific point 4", "specific point 5", "specific point 6"],
      "targetSectionCount": 45
    }},
    {{
      "title": "specific chapter title 3",
      "goal": "chapter-specific learning goal 3",
      "keyPoints": ["specific point 7", "specific point 8", "specific point 9"],
      "targetSectionCount": 41
    }}
  ]
}}
""".strip()


def _build_quiz_packs(request: PlanPackRequest, document_ids: list[str]) -> list[PlanQuizPack]:
    requested_count = _quiz_pack_count(request, len(document_ids))
    if requested_count <= 0:
        return []
    default_choice_language_mode = _default_choice_language_mode(
        request.learningLanguage or infer_learning_language(request.theme, request.targetUser)
    )
    if request.quizPacks:
        return [
            PlanQuizPack(
                id=slugify(spec.id, "quiz"),
                title=spec.title,
                purpose=spec.purpose if spec.purpose in {"key_concepts", "application", "integrated_review"} else "custom",
                questionCount=spec.questionCount,
                difficulty=spec.difficulty,
                sourceDocumentIds=document_ids,
                choiceLanguageMode=spec.choiceLanguageMode or default_choice_language_mode,
            )
            for spec in request.quizPacks[:requested_count]
        ]

    if requested_count == 1:
        return [
            PlanQuizPack(
                id="quiz_single_01",
                title=_localized_label(request.language, "comprehension_check"),
                purpose="key_concepts",
                questionCount=_question_count(request),
                difficulty=request.difficulty,
                sourceDocumentIds=document_ids,
                choiceLanguageMode=default_choice_language_mode,
            )
        ]

    include_integrated = requested_count >= 3
    range_count = max(1, requested_count - 1) if include_integrated else requested_count
    chunks = _split_document_ids(document_ids, range_count)
    question_count = _question_count(request)
    range_titles = RANGE_TITLES.get(range_count) if _is_ja(request.language) else None
    quiz_packs = [
        PlanQuizPack(
            id=f"quiz_range_{index + 1:02d}",
            title=(
                range_titles
                or [
                    f"{_localized_label(request.language, 'comprehension_check')} {item + 1}"
                    for item in range(range_count)
                ]
            )[index],
            purpose=RANGE_PURPOSES[index] if index < len(RANGE_PURPOSES) else "application",
            questionCount=question_count,
            difficulty=request.difficulty,
            sourceDocumentIds=chunk,
            choiceLanguageMode=default_choice_language_mode,
        )
        for index, chunk in enumerate(chunks)
    ]
    if include_integrated and len(quiz_packs) < requested_count:
        quiz_packs.append(
            PlanQuizPack(
                id="quiz_integrated_review",
                title="総合・応用クイズ",
                purpose="integrated_review",
                questionCount=question_count,
                difficulty=request.difficulty,
                sourceDocumentIds=document_ids,
                choiceLanguageMode=default_choice_language_mode,
            )
        )
    return quiz_packs


def _fallback_documents(request: PlanPackRequest) -> list[PlanDocument]:
    count = _document_count(request)
    documents = []
    for index in range(1, count + 1):
        doc_id = f"doc_{index:02d}"
        documents.append(
            PlanDocument(
                id=doc_id,
                title=_fallback_document_title(request, index),
                goal=_fallback_document_goal(request, index),
                keyPoints=_fallback_document_key_points(request, index),
                targetSectionCount=_section_count(request, index=index, total_documents=count),
            )
        )
    return documents


def _normalize_key_points(value: Any, theme: str, index: int) -> list[str]:
    if not isinstance(value, list):
        return [
            f"{theme}の基本事項",
            "重要用語",
            "実践での使い方",
        ]
    points = [str(item).strip() for item in value if str(item).strip()]
    if len(points) < 3:
        points.extend([f"{theme}の重要ポイント{index}", "関連用語", "確認すべき注意点"])
    return points[:6]


def _sanitize_section_count(value: Any, request: PlanPackRequest, *, index: int = 1, total_documents: int = 1) -> int:
    if request.sectionsPerDocument:
        return request.sectionsPerDocument
    try:
        count = int(value)
    except (TypeError, ValueError):
        count = _section_count(request, index=index, total_documents=total_documents)
    return max(SECTION_COUNT_MIN, min(SECTION_COUNT_MAX, count))


def _documents_from_planner_response(data: dict[str, Any], request: PlanPackRequest) -> list[PlanDocument]:
    raw_documents = data.get("documents")
    if not isinstance(raw_documents, list):
        return []

    exact_count = _exact_document_count(request)
    if exact_count == 0:
        return []
    if exact_count is not None:
        raw_documents = raw_documents[:exact_count]
    elif request.scale == "auto":
        raw_documents = raw_documents[:12]

    total_documents = len(raw_documents)
    documents = []
    for index, raw in enumerate(raw_documents, start=1):
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or "").strip()
        goal = str(raw.get("goal") or "").strip()
        if not title or not goal:
            continue
        documents.append(
            PlanDocument(
                id=f"doc_{index:02d}",
                title=title,
                goal=goal,
                keyPoints=_normalize_key_points(raw.get("keyPoints"), request.theme, index),
                targetSectionCount=_sanitize_section_count(
                    raw.get("targetSectionCount"),
                    request,
                    index=index,
                    total_documents=total_documents,
                ),
            )
        )
    return documents


def _short_title_from_planner_response(data: dict[str, Any], request: PlanPackRequest) -> str:
    short_title = str(data.get("shortTitle") or "").strip()
    if not short_title:
        return _fallback_short_title(request)
    short_title = re.sub(r"\s+", "", short_title)
    return short_title[:16] or _fallback_short_title(request)


def _strip_document_title_prefix(title: str, short_title: str) -> str:
    pattern = rf"^{re.escape(short_title)}\s+\d+\.\s*"
    return re.sub(pattern, "", title).strip()


def _prefix_document_titles(documents: list[PlanDocument], short_title: str) -> list[PlanDocument]:
    titled_documents: list[PlanDocument] = []
    for index, document in enumerate(documents, start=1):
        base_title = _strip_document_title_prefix(document.title, short_title)
        titled_documents.append(document.model_copy(update={"title": f"{short_title} {index}. {base_title}"}))
    return titled_documents


def _strict_source_documents(request: PlanPackRequest, source_text: str) -> list[PlanDocument]:
    paragraphs = strict_source_paragraphs(source_text) or [source_text.strip()]
    chunks = split_strict_source_sections(paragraphs)
    return [
        PlanDocument(
            id=f"doc_{index:02d}",
            title=f"資料ファイル {index}" if _is_ja(request.language) else f"Source File {index}",
            goal=(
                f"元資料の段落{start + 1}〜{end}を改変せずに格納する"
                if _is_ja(request.language)
                else f"Store source paragraphs {start + 1}-{end} without rewriting."
            ),
            keyPoints=[],
            targetSectionCount=len(chunk),
        )
        for index, (chunk, start, end) in enumerate(_chunks_with_offsets(chunks), start=1)
    ]


def _chunks_with_offsets(chunks: list[list[str]]) -> list[tuple[list[str], int, int]]:
    offset = 0
    indexed = []
    for chunk in chunks:
        start = offset
        offset += len(chunk)
        indexed.append((chunk, start, offset))
    return indexed


def _document_index(document_id: str, document_ids: list[str]) -> int | None:
    try:
        return document_ids.index(document_id) + 1
    except ValueError:
        match = re.search(r"(\d+)$", document_id)
        return int(match.group(1)) if match else None


def _chapter_range_label(indexes: list[int], total_count: int, language: str | None = None) -> str:
    if not _is_ja(language):
        chapter = _localized_label(language, "chapter")
        chapters = _localized_label(language, "chapters")
        if not indexes:
            return f"{chapters} 1-{total_count}" if total_count > 1 else f"{chapter} 1"
        start, end = min(indexes), max(indexes)
        return f"{chapter} {start}" if start == end else f"{chapters} {start}-{end}"
    if not indexes:
        return f"1〜{total_count}章" if total_count > 1 else "1章"
    start, end = min(indexes), max(indexes)
    return f"{start}章" if start == end else f"{start}〜{end}章"


def _topic_words_for_documents(documents: list[PlanDocument], indexes: list[int], *, max_words: int = 2) -> str:
    words: list[str] = []
    for index in indexes:
        if index < 1 or index > len(documents):
            continue
        document = documents[index - 1]
        source = re.sub(r"^.+?\s+\d+\.\s*", "", document.title).strip()
        parts = re.split(r"[、,・／/と&＆:：\s]+", source)
        for part in parts:
            word = part.strip("（）()「」『』")
            if word and word not in words:
                words.append(word)
            if len(words) >= max_words:
                return "・".join(words)
    return ""


def _title_quiz_packs(quiz_packs: list[PlanQuizPack], documents: list[PlanDocument], short_title: str, language: str | None = None) -> list[PlanQuizPack]:
    document_ids = [document.id for document in documents]
    total_count = len(documents)
    titled_quizzes: list[PlanQuizPack] = []
    range_index = 1
    for quiz_pack in quiz_packs:
        indexes = [
            index
            for document_id in quiz_pack.sourceDocumentIds
            if (index := _document_index(document_id, document_ids)) is not None
        ]
        if quiz_pack.purpose == "integrated_review" or (total_count > 0 and set(quiz_pack.sourceDocumentIds) == set(document_ids)):
            chapter_range = _chapter_range_label(list(range(1, total_count + 1)), total_count, language)
            if _is_ja(language):
                title = f"{short_title} 総合確認（{chapter_range}: 全範囲）"
            else:
                title = (
                    f"{short_title} {_localized_label(language, 'integrated_review')} "
                    f"({chapter_range}: {_localized_label(language, 'full_range')})"
                )
        else:
            chapter_range = _chapter_range_label(indexes, total_count, language)
            topic = _topic_words_for_documents(documents, indexes)
            if _is_ja(language):
                suffix = f": {topic}" if topic else ""
                title = f"{short_title} 理解チェック{range_index}（{chapter_range}{suffix}）"
            else:
                suffix = f": {topic}" if topic else ""
                title = f"{short_title} {_localized_label(language, 'comprehension_check')} {range_index} ({chapter_range}{suffix})"
            range_index += 1
        titled_quizzes.append(quiz_pack.model_copy(update={"title": title}))
    return titled_quizzes


def _fallback_category_context(request: PlanPackRequest) -> str:
    return "\n".join(part for part in [request.theme, request.customInstructions or ""] if part).strip()


def _matches_category_keywords(context: str, category: str) -> bool:
    if not context:
        return False
    return any(pattern.search(context) for pattern in CATEGORY_KEYWORD_PATTERNS[category])


def _fallback_reading_patterns(request: PlanPackRequest) -> list[ReadingPattern]:
    """theme+customInstructions のカテゴリ判定でfallback候補を返す。"""
    context = _fallback_category_context(request)
    if not context:
        return []
    matched_patterns: list[ReadingPattern] = []
    for category in CATEGORY_ORDER:
        if not _matches_category_keywords(context, category):
            continue
        matched_patterns.extend(
            pattern.model_copy(deep=True)
            for pattern in FALLBACK_READING_PATTERNS_BY_CATEGORY[category]
        )
    if not matched_patterns:
        return []
    return _merge_reading_patterns([], matched_patterns, request, max_count=None)


def _matched_fallback_categories(request: PlanPackRequest) -> set[str]:
    context = _fallback_category_context(request)
    if not context:
        return set()
    return {
        category
        for category in CATEGORY_ORDER
        if _matches_category_keywords(context, category)
    }


def _reading_pattern_signature(pattern: ReadingPattern) -> str:
    """読み候補の意味的重複をまとめる署名を返す。"""
    text = " ".join([pattern.id, pattern.title, pattern.description, *pattern.examples]).lower()
    if any(token in text for token in ["難読語", "文学語彙", "たそがれ", "しじま", "literary_difficult_words"]):
        return "literary_difficult_words"
    if any(token in text for token in ["作品名", "人物名", "地名", "百人一首", "芥川龍之介", "literary_proper_nouns"]):
        return "literary_proper_nouns"
    if any(token in text for token in ["試験名", "制度名", "基本情報技術者試験", "日商簿記", "exam_official_names"]):
        return "exam_official_names"
    if any(token in text for token in ["toeic", "fp", "試験で頻出の略語", "exam_abbreviations"]):
        return "exam_abbreviations"
    if any(token in text for token in ["部署名", "役職名", "社内用語", "経営企画部", "執行役員", "business_roles_departments"]):
        return "business_roles_departments"
    if any(token in text for token in ["kpi", "roi", "b2b", "ビジネス略語", "business_abbreviations"]):
        return "business_abbreviations"
    if any(token in text for token in ["漢字語彙", "language_kanji_readings", "ごい", "けいご"]):
        return "language_kanji_readings"
    if any(token in text for token in ["学習語彙", "language_example_readings", "おととい", "あいづち"]):
        return "language_example_readings"
    if any(token in text for token in ["generic_numbers_and_symbols", "第3章", "a/bテスト", "数字・記号・区切り"]):
        return "generic_numbers_and_symbols"
    if any(token in text for token in ["generic_proper_names", "御茶ノ水", "重慶", "固有名詞の読みを一定"]):
        return "generic_proper_names"
    if any(token in text for token in [".git", ".env", ".gitignore", "dot notation", "ドット記法", "ドットファイル"]):
        return "dot_notation"
    if any(token in text for token in ["api", "url", "os", "英略語", "アルファベット"]):
        return "alphabet_abbreviations"
    if any(token in text for token in ["checkout", "install", "command", "コマンド"]):
        return "technical_commands"
    if any(token in text for token in ["camel", "localstorage", "キャメル"]):
        return "camel_case_terms"
    if any(token in text for token in ["version", "slash", "バージョン", "スラッシュ"]):
        return "symbols_and_versions"
    return re.sub(r"\s+", "", pattern.title).lower() or pattern.id


SIGNATURE_CATEGORY_BY_SIGNATURE = {
    "alphabet_abbreviations": "technical",
    "dot_notation": "technical",
    "technical_commands": "technical",
    "camel_case_terms": "technical",
    "symbols_and_versions": "technical",
    "literary_difficult_words": "literature",
    "literary_proper_nouns": "literature",
    "exam_official_names": "qualification",
    "exam_abbreviations": "qualification",
    "business_roles_departments": "business",
    "business_abbreviations": "business",
    "language_kanji_readings": "language",
    "language_example_readings": "language",
    "generic_numbers_and_symbols": "generic",
    "generic_proper_names": "generic",
}


def _pattern_category(pattern: ReadingPattern) -> str | None:
    return FALLBACK_PATTERN_CATEGORY_BY_ID.get(pattern.id) or SIGNATURE_CATEGORY_BY_SIGNATURE.get(
        _reading_pattern_signature(pattern)
    )


def _recommended_allowed_for_pattern(pattern: ReadingPattern, request: PlanPackRequest, documents: list[PlanDocument] | None = None) -> bool:
    """カテゴリ固定ルールで recommended=true を付ける候補か判定。"""
    del documents
    category = _pattern_category(pattern)
    if not category:
        return False
    if category not in _matched_fallback_categories(request):
        return False
    signature = _reading_pattern_signature(pattern)
    return signature in CATEGORY_RECOMMENDED_SIGNATURES.get(category, set())


def _merge_reading_patterns(
    proposed: list[ReadingPattern],
    fallback: list[ReadingPattern],
    request: PlanPackRequest | None = None,
    documents: list[PlanDocument] | None = None,
    *,
    max_count: int | None = None,
) -> list[ReadingPattern]:
    """fallback と LLM 候補を重複排除しつつ統合する。"""
    merged: list[ReadingPattern] = []
    seen_ids: set[str] = set()
    seen_titles: set[str] = set()
    seen_signatures: set[str] = set()
    for pattern in [*fallback, *proposed]:
        title_key = re.sub(r"\s+", "", pattern.title).lower()
        signature = _reading_pattern_signature(pattern)
        if pattern.id in seen_ids or title_key in seen_titles or signature in seen_signatures:
            continue
        if request is not None:
            pattern = pattern.model_copy(update={"recommended": _recommended_allowed_for_pattern(pattern, request, documents)})
        merged.append(pattern)
        seen_ids.add(pattern.id)
        seen_titles.add(title_key)
        seen_signatures.add(signature)
    return merged[:max_count] if max_count is not None else merged


def _apply_recommended_guard(
    patterns: list[ReadingPattern],
    request: PlanPackRequest,
    documents: list[PlanDocument] | None = None,
) -> list[ReadingPattern]:
    """候補配列全体にカテゴリ固定の recommended 上書きを適用する。"""
    guarded: list[ReadingPattern] = []
    for pattern in patterns:
        pattern = pattern.model_copy(update={"recommended": _recommended_allowed_for_pattern(pattern, request, documents)})
        guarded.append(pattern)
    return guarded


def _normalize_reading_example_part(value: str) -> str:
    return re.sub(r"\s+", "", value).strip().lower()


def _source_needs_reading_example(source: str) -> bool:
    compact = re.sub(r"\s+", "", source)
    if not compact:
        return False
    return bool(re.search(r"[A-Za-z0-9一-龯々〆ヵヶ\.／/\\_\-]", compact))


def _valid_reading_examples(raw_examples: Any, *, max_examples: int = 3) -> list[str]:
    if not isinstance(raw_examples, list):
        return []
    valid_examples: list[str] = []
    for raw_example in raw_examples:
        example = str(raw_example).strip()
        if "->" not in example:
            continue
        source, reading = [part.strip() for part in example.split("->", 1)]
        if not source or not reading:
            continue
        if _normalize_reading_example_part(source) == _normalize_reading_example_part(reading):
            continue
        if not _source_needs_reading_example(source):
            continue
        valid_examples.append(f"{source} -> {reading}")
        if len(valid_examples) >= max_examples:
            break
    return valid_examples


def _reading_patterns_from_planner_response(data: dict[str, Any], request: PlanPackRequest) -> list[ReadingPattern]:
    raw_patterns = data.get("proposedReadingPatterns")
    fallback_patterns = _fallback_reading_patterns(request)
    if not isinstance(raw_patterns, list):
        return fallback_patterns

    patterns: list[ReadingPattern] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(raw_patterns, start=1):
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or "").strip()
        description = str(raw.get("description") or "").strip()
        if not title or not description:
            continue
        pattern_id = slugify(str(raw.get("id") or title), f"reading_pattern_{index}")
        if pattern_id in seen_ids:
            pattern_id = f"{pattern_id}_{index}"
        seen_ids.add(pattern_id)
        examples = _valid_reading_examples(raw.get("examples"))
        patterns.append(
            ReadingPattern(
                id=pattern_id,
                title=title,
                description=description,
                examples=examples,
                recommended=bool(raw.get("recommended", False)) if examples else False,
            )
        )
    return _merge_reading_patterns(patterns, fallback_patterns, request)


def _key_points_signature(document: PlanDocument) -> tuple[str, ...]:
    return tuple(point.strip().lower() for point in document.keyPoints)


def _is_generic_title(title: str, theme: str) -> bool:
    compact = re.sub(r"\s+", "", title)
    theme_compact = re.sub(r"\s+", "", theme)
    generic_patterns = [
        r"^第\d+章$",
        r"^第[一二三四五六七八九十]+章$",
        rf"^{re.escape(theme_compact)}第\d+章$",
        rf"^{re.escape(theme_compact)}第[一二三四五六七八九十]+章$",
    ]
    return any(re.match(pattern, compact) for pattern in generic_patterns)


def _validate_planned_documents(documents: list[PlanDocument], request: PlanPackRequest) -> list[str]:
    errors = []
    if request.generationUnit != "quiz" and not documents:
        errors.append("documents must not be empty")
    exact_count = _exact_document_count(request)
    if exact_count is not None and len(documents) != exact_count:
        errors.append(f"documents must contain exactly {exact_count} items")
    if request.generationUnit != "quiz" and exact_count is None and request.scale in SCALE_CHAPTER_RANGES and documents:
        min_count, max_count = SCALE_CHAPTER_RANGES[request.scale]
        if not min_count <= len(documents) <= max_count:
            errors.append(f"documents must contain {min_count}-{max_count} items for {request.scale} scale")
    if documents:
        signatures = {_key_points_signature(document) for document in documents}
        if len(documents) > 1 and len(signatures) == 1:
            errors.append("keyPoints must not be identical across all documents")
        for document in documents:
            if _is_generic_title(document.title, request.theme):
                errors.append(f"document title is too generic: {document.title}")
    return errors


def _gemini_plan_parts(
    request: PlanPackRequest, model: str | None
) -> tuple[str | None, str | None, str, list[PlanDocument], list[ReadingPattern]]:
    data = GeminiClient().generate_json(
        _planner_prompt(request),
        model=model,
        parse_context=LlmJsonParseContext(
            generation_unit="plan",
            model=model or get_settings().planner_model,
            theme=request.theme,
            title=request.theme,
            source_text=request.sourceText,
            additional_instructions=request.customInstructions,
            tts_reading_mode=_effective_request_tts_mode(request),
            language=request.language,
            difficulty=request.difficulty,
            scale=request.scale,
        ),
    )
    title = str(data.get("title") or "").strip() or None
    short_title = _short_title_from_planner_response(data, request)
    description = str(data.get("description") or "").strip() or None
    documents = _documents_from_planner_response(data, request)
    reading_patterns = _reading_patterns_from_planner_response(data, request)
    reading_patterns = _apply_recommended_guard(reading_patterns, request, documents)
    errors = _validate_planned_documents(documents, request)
    if errors:
        raise ValueError("; ".join(errors))
    return title, description, short_title, documents, reading_patterns


def create_course_plan(request: PlanPackRequest, model: str | None = None) -> CoursePlan:
    if request.materialMode == "strict" and request.generationUnit != "document":
        request = request.model_copy(update={"materialMode": "source_only"})
    settings = get_settings()
    pack_id = slugify(request.theme, "sokqa_pack")
    slug = path_token(request.slug or slugify(request.theme, "sokqa-pack").replace("_", "-"), pack_id)
    title = _fallback_pack_title(request)
    description = _description_from_request(request, _fallback_pack_description(request))
    source_text, source_mode = normalize_source(request.sourceText, request.sourceMode)
    source_mode = source_mode_for_material_mode(request.materialMode, source_mode) if source_text else None
    short_title = _fallback_short_title(request)
    tts_mode = _effective_request_tts_mode(request)
    learning_language = request.learningLanguage or infer_learning_language(request.theme, request.targetUser)

    if settings.gemini_provider == "gemini":
        try:
            planned_title, planned_description, short_title, documents, reading_patterns = _gemini_plan_parts(request, model)
            title = planned_title or title
            if request.descriptionMode != "manual":
                description = _description_from_request(request, planned_description or description)
        except Exception:
            documents = _fallback_documents(request)
            reading_patterns = _apply_recommended_guard(_fallback_reading_patterns(request), request, documents)
    else:
        documents = _fallback_documents(request)
        reading_patterns = _apply_recommended_guard(_fallback_reading_patterns(request), request, documents)

    validation_errors = _validate_planned_documents(documents, request)
    if validation_errors:
        raise ValueError("; ".join(validation_errors))

    tts_rules = request.userTtsRules if request.includeTts else []
    if tts_mode != "llm":
        reading_patterns = []
    strict_section_count = None
    strict_file_count = None
    strict_limit_exceeded = False
    if request.materialMode == "strict" and source_text:
        documents = _strict_source_documents(request, source_text)
        strict_section_count = sum(document.targetSectionCount for document in documents)
        strict_file_count = len(documents)
        strict_limit_exceeded = strict_source_limit_error(strict_file_count) is not None
    documents = _prefix_document_titles(documents, short_title)
    quiz_packs = _build_quiz_packs(request, [document.id for document in documents])
    quiz_packs = _apply_requested_choice_language_modes(quiz_packs, request, learning_language)
    quiz_packs = _title_quiz_packs(quiz_packs, documents, short_title, request.language)

    plan = CoursePlan(
        id=pack_id,
        creatorId=resolve_creator_id(request.creatorId),
        creatorDisplayName=request.creatorDisplayName,
        contentId=path_token(request.contentId or new_opaque_id("cnt"), "cnt_default"),
        slug=slug,
        shortTitle=short_title,
        title=title,
        description=description,
        language=request.language,
        learningLanguage=learning_language,
        customInstructions=request.customInstructions,
        targetUser=request.targetUser,
        difficulty=request.difficulty,
        scale=request.scale,
        structurePolicy=request.structurePolicy,
        generationUnit=request.generationUnit,
        docCount=request.docCount,
        quizCount=request.quizCount,
        materialMode=request.materialMode,
        questionCount=request.questionCount,
        sectionsPerDocument=request.sectionsPerDocument,
        globalTagsMode=request.globalTagsMode,
        manualGlobalTags=request.manualGlobalTags,
        descriptionMode=request.descriptionMode,
        manualDescription=request.manualDescription,
        descriptionIncludeDate=request.descriptionIncludeDate,
        descriptionIncludeAiDisclaimer=request.descriptionIncludeAiDisclaimer,
        answerPositionMode=request.answerPositionMode,
        author=settings.sokqa_author,
        enableTtsOptimize=request.includeTts and request.enableTtsOptimize,
        ttsReadingMode=tts_mode,
        ttsLanguageSettings=request.ttsLanguageSettings if tts_mode == "multilingual" else None,
        model=request.model,
        docModel=request.docModel,
        quizModel=request.quizModel,
        plannerModel=request.plannerModel,
        sourceText=source_text,
        sourceMode=source_mode,
        strictSourceSectionCount=strict_section_count,
        strictSourceFileCount=strict_file_count,
        strictSourceMaxFiles=STRICT_MAX_DOCUMENT_FILES if strict_file_count is not None else None,
        strictSourceLimitExceeded=strict_limit_exceeded,
        documents=documents,
        quizPacks=quiz_packs,
        ttsRules=tts_rules,
        proposedReadingPatterns=reading_patterns,
    )
    return plan.model_copy(update={"globalTags": _global_tags_from_request(request, plan)})
