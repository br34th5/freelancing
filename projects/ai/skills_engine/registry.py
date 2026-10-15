from __future__ import annotations

from pathlib import Path
from typing import Optional

from .loader import SkillLoader
from .models import Skill
from .memory import SessionMemory, SkillPersister


class SkillRegistry:
    def __init__(self, learned_dir: Optional[str | Path] = None) -> None:
        self._skills: dict[str, Skill] = {}
        self._learned_dir = Path(learned_dir) if learned_dir else None
        if self._learned_dir:
            try:
                self.load_from_directory(str(self._learned_dir))
            except FileNotFoundError:
                pass

    def persist(self, memory: SessionMemory, name: Optional[str] = None) -> Optional[Path]:
        if not self._learned_dir:
            return None
        persister = SkillPersister(self._learned_dir)
        path = persister.save_session(memory, name)
        for skill in SkillLoader.from_directory(self._learned_dir):
            if skill.name not in self._skills:
                self.register(skill)
        return path

    def __len__(self) -> int:
        return len(self._skills)

    def __contains__(self, name: str) -> bool:
        return name in self._skills

    def register(self, skill: Skill) -> None:
        self._skills[skill.name] = skill

    def unregister(self, name: str) -> None:
        self._skills.pop(name, None)

    def get(self, name: str) -> Optional[Skill]:
        return self._skills.get(name)

    def list_skills(self) -> list[Skill]:
        return sorted(self._skills.values(), key=lambda s: s.name)

    def load_from_directory(self, path: str) -> list[Skill]:
        skills = SkillLoader.from_directory(path)
        for s in skills:
            self.register(s)
        return skills

    def load_from_file(self, path: str) -> Skill:
        skill = SkillLoader.from_file(path)
        self.register(skill)
        return skill

    def load_from_github(self, repo: str, skill_path: str, ref: str = "main") -> Skill:
        skill = SkillLoader.from_github(repo, skill_path, ref)
        self.register(skill)
        return skill

    def load_from_url(self, url: str, name: Optional[str] = None) -> Skill:
        skill = SkillLoader.from_url(url, name)
        self.register(skill)
        return skill

    def search(self, query: str, top_k: int = 5, threshold: float = 0.5) -> list[Skill]:
        scored = []
        for skill in self._skills.values():
            score = skill.matches_query(query)
            if score >= threshold:
                scored.append((score, skill))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [s for _, s in scored[:top_k]]

    def get_with_parents(self, name: str) -> list[Skill]:
        skill = self.get(name)
        if not skill:
            return []
        chain = [skill]
        seen = {name}
        current = skill
        while current.metadata.parent and current.metadata.parent not in seen:
            parent = self.get(current.metadata.parent)
            if not parent:
                break
            chain.insert(0, parent)
            seen.add(parent.name)
            current = parent
        return chain

    def resolve_context(self, query: str, max_skills: int = 3, max_chars: int = 15000) -> str:
        matched = self.search(query, top_k=max_skills)
        if not matched:
            return ""
        parts = []
        total = 0
        for skill in matched:
            truncated = skill.truncate(max_chars=max_chars // max_skills)
            section = f"## Skill: {skill.name}\n\n{truncated}"
            if total + len(section) > max_chars:
                break
            parts.append(section)
            total += len(section)
        return "\n\n---\n\n".join(parts)

    def summary(self) -> str:
        lines = [f"SkillRegistry: {len(self)} skills loaded"]
        for s in self.list_skills():
            parent = f" (child of {s.metadata.parent})" if s.metadata.parent else ""
            lines.append(f"  - {s.name}{parent}: {s.description[:80]}")
        return "\n".join(lines)
