from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field


class SkillMetadata(BaseModel):
    parent: Optional[str] = None
    source: Optional[str] = None
    version: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    extra: dict[str, str] = Field(default_factory=dict)


class Skill(BaseModel):
    name: str
    description: str
    content: str
    metadata: SkillMetadata = Field(default_factory=SkillMetadata)
    source_type: str = "local"
    source_path: Optional[str] = None
    loaded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    hash: Optional[str] = None

    def matches_query(self, query: str) -> float:
        query_lower = query.lower()
        tokens = query_lower.split()
        text = f"{self.name} {self.description}".lower()
        score = 0.0
        if self.name.lower() in query_lower or query_lower in self.name.lower():
            score += 3.0
        for token in tokens:
            if token in text:
                score += 1.0
        if self.metadata.parent and self.metadata.parent.lower() in query_lower:
            score += 0.5
        for tag in self.metadata.tags:
            if tag.lower() in query_lower:
                score += 0.5
        return score

    def truncate(self, max_chars: int = 8000) -> str:
        if len(self.content) <= max_chars:
            return self.content
        return self.content[:max_chars] + "\n\n... [truncated]"
