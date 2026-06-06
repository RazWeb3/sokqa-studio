import re
import unicodedata
from uuid import uuid4


def slugify(value: str, fallback: str = "pack") -> str:
    normalized = unicodedata.normalize("NFKC", value).lower()
    normalized = re.sub(r"[^a-z0-9]+", "_", normalized)
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return normalized or fallback


def new_job_id() -> str:
    return uuid4().hex[:16]


def new_opaque_id(prefix: str, length: int = 10) -> str:
    return f"{prefix}_{uuid4().hex[:length]}"


def path_token(value: str, fallback: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip()
    normalized = re.sub(r"[^A-Za-z0-9_-]+", "-", normalized)
    normalized = re.sub(r"-+", "-", normalized).strip("-_")
    return normalized or fallback
