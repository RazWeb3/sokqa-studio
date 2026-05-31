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
