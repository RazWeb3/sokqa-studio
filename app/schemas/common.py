from typing import Literal

from pydantic import BaseModel, Field


Scale = Literal["quick", "standard"]
Difficulty = Literal["beginner", "standard", "advanced"]
QuizPurpose = Literal["key_concepts", "application", "integrated_review", "custom"]
TtsReadingMode = Literal["rule", "llm", "auto"]
SourceMode = Literal["document_only", "document_reference"]


class TtsRule(BaseModel):
    source: str = Field(..., min_length=1, max_length=80)
    reading: str = Field(..., min_length=1, max_length=120)
    note: str | None = Field(default=None, max_length=200)
