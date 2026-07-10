"""Language Learning 専用 Planner 処理（Phase 3: Planner 移設）。

planner.py から切り出した語学専用ロジックをここへ集約する。
Standard 側は planner.py に残り、generate_pack は統合判定（is_language_learning_plan）に基づき
呼び出し先を切り替える（Phase 2 の Strategy 選択と同じく、Phase 3 時点は既存挙動を維持）。

系統A（外国語学習）と系統B（日本語学習）の双方をここで扱う。
"""

from app.schemas.request import PlanPackRequest


def _base_language(language: str | None) -> str:
    return (language or "").split("-")[0].strip().lower()


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


def _language_learning_planner_objective(request: PlanPackRequest) -> str:
    """言語学習教材の目的関数ブロック（学習場面ベース）。

    言語のハードコードは禁止。packLanguage / learningLanguage は変数として使い、
    ja/en 等の固定ペアに依存しない。
    """
    learning_language = (
        request.learningLanguage or infer_learning_language(request.theme, request.targetUser) or "not specified"
    )
    pack_language = request.language
    return f"""
# この教材の目的関数（言語学習: 学習場面ベース）
この学習パックは「{request.theme}」を題材にして、学習言語 {learning_language} を学ぶための教材である（パック言語 {pack_language} は意味・使う場面・ニュアンスの補助説明に用いる）。
- テーマを説明する章を作るのではなく、テーマを題材にして {learning_language} を学ぶための章構成を作る。
- 章タイトルは「学習フレーズの利用場面」を表すこと。
  Bad: 「海外旅行とは」「飛行機について」「ホテルについて」（説明対象が主語）
  Good: 「空港で使う基本表現」「チェックインで使う表現」「機内で使う表現」「ホテルで使う表現」（学習場面が主語）
- document.goal は「その場面で {learning_language} の表現を使えるようになる」形にする。
- document.keyPoints は、その場面で扱う代表的なフレーズ／表現のまとまり（利用場面のビート）にすること。テーマの知識項目の列挙にしない。
""".strip()


def planner_objective(request: PlanPackRequest) -> str:
    """語学学習用のプランナー目的関数を返す（Planner 側の _planner_objective の LL 分岐に相当）。"""
    return _language_learning_planner_objective(request)
