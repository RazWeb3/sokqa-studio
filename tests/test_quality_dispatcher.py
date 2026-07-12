from app.schemas.quality import QualityIssue
from app.services.generation.strategies.language_learning import LanguageLearningStrategy
from app.services.generation.strategies.standard import StandardStrategy
from app.services.quality.context import QualityContext
from app.services.quality.dispatcher import check_quality


def test_dispatcher_runs_common_and_standard_only(monkeypatch):
    calls = []
    monkeypatch.setattr("app.services.quality.dispatcher.check_common_structure", lambda *_args, **_kwargs: calls.append("common") or [])
    check_quality(QualityContext(StandardStrategy()), "x.json", {}, mode="text", standard_check=lambda _: calls.append("standard") or [], language_learning_check=lambda _: calls.append("ll") or [])
    assert calls == ["common", "standard"]


def test_dispatcher_runs_common_and_language_learning_only(monkeypatch):
    calls = []
    monkeypatch.setattr("app.services.quality.dispatcher.check_common_structure", lambda *_args, **_kwargs: calls.append("common") or [])
    check_quality(QualityContext(LanguageLearningStrategy()), "x.json", {}, mode="text", standard_check=lambda _: calls.append("standard") or [], language_learning_check=lambda _: calls.append("ll") or [])
    assert calls == ["common", "ll"]
