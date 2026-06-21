import re

from app.schemas.sokqa import CoursePlan, PlanDocument, PlanQuizPack

_GENERIC_JA = {
    "入門",
    "基礎",
    "基本",
    "学習",
    "確認",
    "理解",
    "重要",
    "領域",
    "初心者",
    "初学者",
    "総合確認",
    "理解チェック",
    "重要概念",
    "応用",
    "章",
    "節",
}
_GENERIC_EN = {
    "intro",
    "basic",
    "basics",
    "learning",
    "review",
    "check",
    "beginner",
    "standard",
    "advanced",
    "chapter",
    "section",
    "integrated",
    "key",
    "concepts",
    "application",
}


def _language_base(language: str | None) -> str:
    return (language or "ja").split("-")[0].lower()


def _unique_limited(values: list[str], limit: int = 3) -> list[str]:
    tags: list[str] = []
    seen: set[str] = set()
    for value in values:
        tag = re.sub(r"\s+", " ", str(value)).strip(" 　,、。:：/／|｜・-")
        if not tag:
            continue
        key = tag.casefold()
        if key in seen:
            continue
        tags.append(tag[:40])
        seen.add(key)
        if len(tags) >= limit:
            break
    return tags


def _is_structural_tag(tag: str) -> bool:
    compact = re.sub(r"\s+", "", tag).strip()
    if not compact:
        return True
    if re.fullmatch(r"\d+[\.．]?", compact):
        return True
    if re.fullmatch(r"\d+章|第\d+章|第[一二三四五六七八九十百]+章", compact):
        return True
    if re.fullmatch(r"(chapter|section)\d+", compact, flags=re.IGNORECASE):
        return True
    if compact in _GENERIC_JA or compact.casefold() in _GENERIC_EN:
        return True
    if len(compact) <= 1:
        return True
    if not re.search(r"[A-Za-z0-9一-龯々ァ-ヶー]", compact):
        return True
    return False


def _clean_tags(values: list[str], *, limit: int = 3) -> list[str]:
    return _unique_limited([value for value in values if not _is_structural_tag(str(value))], limit)


def _ja_candidates(*texts: str) -> list[str]:
    candidates: list[str] = []
    joined = " ".join(text for text in texts if text)
    for match in re.findall(r"[A-Za-z0-9一-龯々ァ-ヶー][A-Za-z0-9一-龯々ァ-ヶー+.#_-]{1,}", joined):
        word = match.strip()
        if word in _GENERIC_JA or word.isdigit():
            continue
        if "ITパスポート" in word and word != "ITパスポート":
            candidates.append("ITパスポート試験" if "試験" in word else "ITパスポート")
        candidates.append(word)
    return candidates


def _latin_candidates(*texts: str) -> list[str]:
    candidates: list[str] = []
    joined = " ".join(text for text in texts if text)
    for match in re.findall(r"[A-Za-z][A-Za-z0-9+.#_-]{1,}", joined):
        word = match.strip()
        if word.casefold() in _GENERIC_EN:
            continue
        candidates.append(word)
    return candidates


def course_global_tags(plan: CoursePlan) -> list[str]:
    if _language_base(plan.language) == "ja":
        return _clean_tags(_ja_candidates(plan.shortTitle or "", plan.title, plan.description) or [plan.shortTitle or plan.title])
    return _clean_tags(_latin_candidates(plan.shortTitle or "", plan.title, plan.description) or [plan.shortTitle or plan.title])


def _plan_tags_or_fallback(plan: CoursePlan, fallback_candidates: list[str]) -> list[str]:
    plan_tags = _clean_tags(plan.globalTags)
    if plan_tags:
        return plan_tags
    fallback = _clean_tags(fallback_candidates)
    if fallback:
        return fallback
    return _clean_tags([plan.shortTitle or plan.title or plan.id])


def document_global_tags(plan: CoursePlan, document: PlanDocument) -> list[str]:
    if _language_base(plan.language) == "ja":
        return _plan_tags_or_fallback(
            plan,
            _ja_candidates(document.goal, *document.keyPoints, document.title, plan.shortTitle or "", plan.title),
        )
    return _plan_tags_or_fallback(
        plan,
        _latin_candidates(document.goal, *document.keyPoints, document.title, plan.shortTitle or "", plan.title),
    )


def quiz_global_tags(plan: CoursePlan, quiz_pack: PlanQuizPack) -> list[str]:
    purpose_label = {
        "key_concepts": "重要概念" if _language_base(plan.language) == "ja" else "Key concepts",
        "application": "応用" if _language_base(plan.language) == "ja" else "Application",
        "integrated_review": "総合確認" if _language_base(plan.language) == "ja" else "Integrated review",
        "custom": "確認" if _language_base(plan.language) == "ja" else "Review",
    }.get(quiz_pack.purpose, quiz_pack.purpose)
    if _language_base(plan.language) == "ja":
        return _plan_tags_or_fallback(
            plan,
            _ja_candidates(purpose_label, quiz_pack.title, plan.shortTitle or "", plan.title),
        )
    return _plan_tags_or_fallback(
        plan,
        _latin_candidates(purpose_label, quiz_pack.title, plan.shortTitle or "", plan.title),
    )
