from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, Optional, Union

from .formatter import SkillFormatter
from .loader import SkillLoader
from .memory import SessionMemory
from .models import Skill
from .registry import SkillRegistry


Message = dict[str, str]
Messages = list[Message]


class SkilledAgent:
    def __init__(
        self,
        name: str = "agent",
        registry: Optional[SkillRegistry] = None,
        llm_fn: Optional[Callable[[Messages], str]] = None,
        base_prompt: Optional[str] = None,
        max_skills: int = 3,
        learned_dir: Optional[Union[str, Path]] = None,
        max_messages: int = 40,
    ) -> None:
        self.name = name
        if registry is not None:
            self.registry = registry
        else:
            self.registry = SkillRegistry(learned_dir=learned_dir)
        self.formatter = SkillFormatter(self.registry)
        self.llm_fn = llm_fn
        self.base_prompt = base_prompt
        self.max_skills = max_skills
        self.max_messages = max_messages
        self.conversation: Messages = []
        self._session: Optional[SessionMemory] = None

    def start_session(self) -> SessionMemory:
        if self._session is not None:
            self.end_session(persist=False)
        self._session = SessionMemory()
        return self._session

    @property
    def session(self) -> Optional[SessionMemory]:
        return self._session

    @contextmanager
    def session_scope(self) -> Iterator[SessionMemory]:
        sess = self.start_session()
        try:
            yield sess
            self.end_session(persist=True)
        except Exception:
            self.end_session(persist=False)
            raise

    def end_session(self, persist: bool = True) -> Optional[str]:
        if not self._session:
            return None
        path = None
        if persist and self._session.discoveries:
            path = self.registry.persist(self._session)
        self._session = None
        return str(path) if path else None

    def add_skill_from_file(self, path: Union[str, Path]) -> Skill:
        return self.registry.load_from_file(path)

    def add_skill_from_directory(self, path: Union[str, Path]) -> list[Skill]:
        return self.registry.load_from_directory(path)

    def add_skill_from_github(self, repo: str, skill_path: str, ref: str = "main") -> Skill:
        return self.registry.load_from_github(repo, skill_path, ref)

    def add_skill_from_url(self, url: str, name: Optional[str] = None) -> Skill:
        return self.registry.load_from_url(url, name)

    def add_skill_from_text(self, raw: str, name: Optional[str] = None) -> Skill:
        skill = SkillLoader.from_text(raw)
        if name:
            skill.name = name
        self.registry.register(skill)
        return skill

    def build_messages(self, user_message: str) -> Messages:
        system_msg = self.formatter.as_system_prompt(
            query=user_message,
            base_prompt=self.base_prompt,
            max_skills=self.max_skills,
        )
        tail = [m for m in self.conversation if m["role"] != "system"]
        budget = self.max_messages - (1 if system_msg else 0) - 1
        tail = tail[-budget:] if budget > 0 else []
        msgs: Messages = []
        if system_msg:
            msgs.append({"role": "system", "content": system_msg})
        msgs.extend(tail)
        msgs.append({"role": "user", "content": user_message})
        return msgs

    def _remember_turn(self, user_message: str, response: str) -> None:
        system = [m for m in self.conversation if m["role"] == "system"][:1]
        non_system = [m for m in self.conversation if m["role"] != "system"]
        non_system.append({"role": "user", "content": user_message})
        non_system.append({"role": "assistant", "content": response})
        keep = max(0, self.max_messages - len(system))
        self.conversation = system + non_system[-keep:]

    def chat(self, user_message: str) -> str:
        if not self.llm_fn:
            matched = self.registry.search(user_message, top_k=self.max_skills)
            if matched:
                skills_text = ", ".join(s.name for s in matched)
                return (
                    f"[{self.name}] Matched skills: {skills_text}\n\n"
                    + self.registry.resolve_context(user_message, self.max_skills)
                )
            return f"[{self.name}] No matching skills for: {user_message}"

        messages = self.build_messages(user_message)
        response = self.llm_fn(messages)
        self._remember_turn(user_message, response)
        return response

    def reset_conversation(self) -> None:
        self.conversation = []

    def get_skill_content(self, name: str) -> Optional[str]:
        chain = self.registry.get_with_parents(name)
        if not chain:
            return None
        return "\n\n---\n\n".join(s.content for s in chain)

    def status(self) -> str:
        session_info = f" | session={'active' if self._session else 'none'}"
        return (
            f"Agent '{self.name}' | {len(self.registry)} skills "
            f"| llm={'yes' if self.llm_fn else 'no'} "
            f"| turns={len(self.conversation)}/{self.max_messages}"
            f"{session_info}"
        )