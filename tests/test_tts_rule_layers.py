from app.config import get_settings
from app.schemas.common import TtsRule
from app.schemas.request import GeneratePackRequest, PlanPackRequest
from app.services.pack_agent import generate_pack, plan_pack
from app.services.tts_optimizer import _combined_rules, _speech_text
from app.services.tts_rules import load_configured_tts_rules


def test_system_rules_apply_when_user_rules_missing(tmp_path, monkeypatch) -> None:
    system_rules = tmp_path / "tts_rules.json"
    missing_user_rules = tmp_path / "missing_user_rules.json"
    system_rules.write_text('{"git init": "ギット イニット"}', encoding="utf-8")

    settings = get_settings()
    monkeypatch.setattr(settings, "tts_rules_path", str(system_rules))
    monkeypatch.setattr(settings, "tts_user_rules_path", str(missing_user_rules))

    rules = load_configured_tts_rules()
    assert _speech_text("git init を実行します", rules) == "ギット イニット を実行します"


def test_user_rules_override_system_rules(tmp_path, monkeypatch) -> None:
    system_rules = tmp_path / "tts_rules.json"
    user_rules = tmp_path / "tts_user_rules.json"
    system_rules.write_text('{"git init": "ギット イニット"}', encoding="utf-8")
    user_rules.write_text('{"git init": "ジット イニット"}', encoding="utf-8")

    settings = get_settings()
    monkeypatch.setattr(settings, "tts_rules_path", str(system_rules))
    monkeypatch.setattr(settings, "tts_user_rules_path", str(user_rules))

    rules = load_configured_tts_rules()
    assert _speech_text("git init を実行します", rules) == "ジット イニット を実行します"


def test_empty_user_rules_keep_system_rules(tmp_path, monkeypatch) -> None:
    system_rules = tmp_path / "tts_rules.json"
    user_rules = tmp_path / "tts_user_rules.json"
    system_rules.write_text('{"git init": "ギット イニット"}', encoding="utf-8")
    user_rules.write_text("", encoding="utf-8")

    settings = get_settings()
    monkeypatch.setattr(settings, "tts_rules_path", str(system_rules))
    monkeypatch.setattr(settings, "tts_user_rules_path", str(user_rules))

    rules = load_configured_tts_rules()
    assert _speech_text("git init を実行します", rules) == "ギット イニット を実行します"


def test_plan_rules_override_user_and_system_rules(tmp_path, monkeypatch) -> None:
    system_rules = tmp_path / "tts_rules.json"
    user_rules = tmp_path / "tts_user_rules.json"
    system_rules.write_text('{"git init": "ギット イニット"}', encoding="utf-8")
    user_rules.write_text('{"git init": "ジット イニット"}', encoding="utf-8")

    settings = get_settings()
    monkeypatch.setattr(settings, "tts_rules_path", str(system_rules))
    monkeypatch.setattr(settings, "tts_user_rules_path", str(user_rules))

    rules = _combined_rules([TtsRule(source="git init", reading="ジーアイティー イニット")])
    assert _speech_text("git init を実行します", rules) == "ジーアイティー イニット を実行します"


def test_enable_tts_optimize_false_keeps_tts_absent() -> None:
    plan = plan_pack(
        PlanPackRequest(
            theme="git init 基礎講座",
            targetUser="Gitを初めて使う開発者",
            scale="quick",
            includeTts=True,
            enableTtsOptimize=False,
        )
    )
    generated = generate_pack(GeneratePackRequest(plan=plan, persist=False))
    for file in generated.files:
        if file.kind == "document":
            assert all("tts" not in item for item in file.content["documents"])
        if file.kind == "quiz":
            assert all("tts" not in item for item in file.content["questions"])


def test_system_dictionary_does_not_include_context_dependent_number_rules(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "tts_rules_path", "tts_rules.json")
    monkeypatch.setattr(settings, "tts_user_rules_path", "")

    rules = load_configured_tts_rules()
    sources = {rule.source for rule in rules}
    assert {"1本", "1時", "9時", "20歳"}.isdisjoint(sources)

    text = _speech_text("1本の資料を1時に確認し、9時に20歳の例を見ます。", rules)
    assert "1本" in text
    assert "1時" in text
    assert "9時" in text
    assert "20歳" in text


def test_replacement_prefers_longer_sources_for_git_commands() -> None:
    rules = [
        TtsRule(source="git", reading="ギット"),
        TtsRule(source="git init", reading="ギット イニット"),
    ]
    assert _speech_text("git init を実行します", rules) == "ギット イニット を実行します"


def test_replacement_prefers_longer_sources_for_dot_git_words() -> None:
    rules = [
        TtsRule(source=".git", reading="ドット ギット"),
        TtsRule(source=".gitignore", reading="ドット ギットイグノア"),
        TtsRule(source=".gitattributes", reading="ドット ギットアトリビューツ"),
    ]
    text = _speech_text(".gitignore と .gitattributes と .git を確認します", rules)
    assert text == "ドット ギットイグノア と ドット ギットアトリビューツ と ドット ギット を確認します"


def test_system_dictionary_dot_words_and_extensions() -> None:
    rules = load_configured_tts_rules()
    text = _speech_text(".git .env .gitignore config.json file.yaml file.yml", rules)
    assert text == "ドット ギット ドット イーエヌブイ ドット ギットイグノア configドット ジェイソン file.yaml file.yml"


def test_japanese_fixed_reading_rule_applies() -> None:
    rules = [TtsRule(source="読替語", reading="よみかえご")]
    text = _speech_text("読替語を確認します", rules)
    assert text == "よみかえごを確認します"


def test_json_casing_rules_are_separate() -> None:
    rules = load_configured_tts_rules()
    text = _speech_text("JSON と config.json と json を確認します", rules)
    assert text == "ジェイソン と configドット ジェイソン と json を確認します"


def test_parenthetical_source_after_existing_reading_is_not_replaced_twice() -> None:
    rules = [TtsRule(source="ROE", reading="アールオーイー")]

    text = _speech_text("アールオーイー（ROE）は収益性の指標です。", rules)

    assert text == "アールオーイー（ROE）は収益性の指標です。"
    assert "アールオーイー（アールオーイー）" not in text


def test_canonical_term_with_meaning_parenthetical_is_replaced_once() -> None:
    rules = [TtsRule(source="ROE", reading="アールオーイー")]

    text = _speech_text("ROE（自己資本利益率）は収益性の指標です。", rules)

    assert text == "アールオーイー（自己資本利益率）は収益性の指標です。"
