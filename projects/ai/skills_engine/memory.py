from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from .models import Skill, SkillMetadata


class Discovery(BaseModel):
    category: str
    topic: str
    observation: str
    solution: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    confidence: float = 1.0
    session_id: Optional[str] = None


class SessionMemory:
    def __init__(self, session_id: Optional[str] = None) -> None:
        self.session_id = session_id or datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        self.discoveries: list[Discovery] = []
        self._started = datetime.now(timezone.utc)

    def record(self, category: str, topic: str, observation: str,
               solution: Optional[str] = None, tags: Optional[list[str]] = None,
               confidence: float = 1.0) -> None:
        self.discoveries.append(Discovery(
            category=category,
            topic=topic,
            observation=observation,
            solution=solution,
            tags=tags or [],
            confidence=confidence,
            session_id=self.session_id,
        ))

    def record_success(self, topic: str, observation: str,
                       solution: Optional[str] = None,
                       tags: Optional[list[str]] = None) -> None:
        self.record("success", topic, observation, solution, tags, confidence=1.0)

    def record_failure(self, topic: str, observation: str,
                       workaround: Optional[str] = None,
                       tags: Optional[list[str]] = None) -> None:
        self.record("failure", topic, observation, workaround, tags, confidence=0.8)

    def record_pattern(self, topic: str, observation: str,
                       tags: Optional[list[str]] = None) -> None:
        self.record("pattern", topic, observation, tags=tags, confidence=0.9)

    def to_skill(self, name: Optional[str] = None) -> Skill:
        if not name:
            name = f"session-{self.session_id}"

        by_cat: dict[str, list[Discovery]] = {}
        for d in self.discoveries:
            by_cat.setdefault(d.category, []).append(d)

        lines = []
        lines.append(f"# Session Learnings — {self.session_id}")
        lines.append("")
        lines.append(f"Session ran: {self._started.strftime('%Y-%m-%d %H:%M UTC')}")
        lines.append(f"Total discoveries: {len(self.discoveries)}")
        lines.append("")

        if "success" in by_cat:
            lines.append("## What Worked")
            lines.append("")
            for d in by_cat["success"]:
                lines.append(f"### {d.topic}")
                lines.append(f"{d.observation}")
                if d.solution:
                    lines.append(f"\n**Solution:** {d.solution}")
                lines.append("")

        if "failure" in by_cat:
            lines.append("## What Failed & Workarounds")
            lines.append("")
            for d in by_cat["failure"]:
                lines.append(f"### {d.topic}")
                lines.append(f"{d.observation}")
                if d.solution:
                    lines.append(f"\n**Workaround:** {d.solution}")
                lines.append("")

        if "pattern" in by_cat:
            lines.append("## Patterns Observed")
            lines.append("")
            for d in by_cat["pattern"]:
                lines.append(f"- **{d.topic}**: {d.observation}")
            lines.append("")

        all_tags = sorted({t for d in self.discoveries for t in d.tags})

        desc_parts = []
        for d in self.discoveries[:5]:
            desc_parts.append(d.topic)
        description = "; ".join(desc_parts)

        return Skill(
            name=name,
            description=description[:300],
            content="\n".join(lines),
            metadata=SkillMetadata(
                source=f"session:{self.session_id}",
                tags=all_tags,
                extra={"auto_generated": "true"},
            ),
            source_type="learned",
        )


class SkillPersister:
    def __init__(self, base_dir: str | Path) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def save_session(self, memory: SessionMemory, name: Optional[str] = None) -> Path:
        skill = memory.to_skill(name)
        return self.save_skill(skill)

    def save_skill(self, skill: Skill) -> Path:
        safe_name = re.sub(r"[^a-zA-Z0-9_-]", "-", skill.name).strip("-") or "skill"
        skill_dir = self.base_dir / safe_name
        skill_dir.mkdir(parents=True, exist_ok=True)

        tags_line = ""
        if skill.metadata.tags:
            tags_str = ", ".join(f'"{t}"' for t in skill.metadata.tags)
            tags_line = f"tags: [{tags_str}]"

        parent_line = ""
        if skill.metadata.parent:
            parent_line = f"parent: {skill.metadata.parent}"

        meta_lines = [l for l in [parent_line, tags_line] if l]
        meta_block = "\n".join(meta_lines)

        frontmatter = f"""---
name: {skill.name}
description: >-
  {skill.description}
metadata:
  {meta_block.replace(chr(10), chr(10) + "  ")}
---"""

        content = f"{frontmatter}\n\n{skill.content}\n"
        out_path = skill_dir / "SKILL.md"
        out_path.write_text(content, encoding="utf-8")
        return out_path

    def load_all(self) -> list[Skill]:
        from .loader import SkillLoader
        return SkillLoader.from_directory(self.base_dir)
