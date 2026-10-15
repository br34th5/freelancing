from __future__ import annotations

from typing import Optional

from .models import Skill
from .registry import SkillRegistry


class SkillFormatter:
    def __init__(self, registry: SkillRegistry) -> None:
        self.registry = registry

    def as_system_prompt(
        self,
        query: Optional[str] = None,
        base_prompt: Optional[str] = None,
        max_skills: int = 3,
        max_chars: int = 15000,
    ) -> str:
        parts = []
        if base_prompt:
            parts.append(base_prompt)

        if query:
            context = self.registry.resolve_context(query, max_skills, max_chars)
            if context:
                parts.append("# Active Skills\n\nThe following skills are relevant to the current task:\n")
                parts.append(context)
        else:
            skill_list = self.registry.list_skills()
            if skill_list:
                summary = self._format_skill_catalog(skill_list)
                parts.append("# Available Skills\n")
                parts.append(summary)

        return "\n\n".join(parts)

    def as_openai_messages(
        self,
        user_message: str,
        base_prompt: Optional[str] = None,
        max_skills: int = 3,
    ) -> list[dict[str, str]]:
        system = self.as_system_prompt(
            query=user_message,
            base_prompt=base_prompt,
            max_skills=max_skills,
        )
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user_message})
        return messages

    def as_anthropic_messages(
        self,
        user_message: str,
        base_prompt: Optional[str] = None,
        max_skills: int = 3,
    ) -> tuple[str, list[dict]]:
        system = self.as_system_prompt(
            query=user_message,
            base_prompt=base_prompt,
            max_skills=max_skills,
        )
        messages = [{"role": "user", "content": user_message}]
        return system, messages

    def as_tool_definitions(self) -> list[dict]:
        tools = []
        for skill in self.registry.list_skills():
            tools.append({
                "type": "function",
                "function": {
                    "name": f"use_skill_{skill.name.replace('-', '_')}",
                    "description": skill.description[:1024],
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "The specific question or task for this skill",
                            }
                        },
                        "required": ["query"],
                    },
                },
            })
        return tools

    def _format_skill_catalog(self, skills: list[Skill]) -> str:
        lines = []
        for s in skills:
            parent = f" (extends: {s.metadata.parent})" if s.metadata.parent else ""
            lines.append(f"- **{s.name}**{parent}: {s.description[:200]}")
        return "\n".join(lines)
