"""Tests for the skills_engine package.

Covers loader.py (YAML frontmatter parsing) and agent.py (conversation
state, message cap, session lifecycle, skill loading).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECTS_AI = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECTS_AI))

from skills_engine.agent import SkilledAgent
from skills_engine.loader import SkillLoader, SkillParseError
from skills_engine.models import Skill


class TestParseFrontmatter:
    def test_minimal(self):
        raw = "---\nname: foo\n---\nbody\n"
        s = SkillLoader.from_text(raw)
        assert s.name == "foo"
        assert s.content == "body"

    def test_tags_list(self):
        raw = "---\nname: x\ntags: [a, b, c]\n---\nb\n"
        s = SkillLoader.from_text(raw)
        assert s.metadata.tags == ["a", "b", "c"]

    def test_folded_description(self):
        raw = "---\nname: x\ndescription: >-\n  one two three\n---\nb\n"
        s = SkillLoader.from_text(raw)
        assert "one two three" in s.description
        assert "\n" not in s.description

    def test_multiline_mapping(self):
        raw = (
            "---\n"
            "name: x\n"
            "metadata:\n"
            "  parent: y\n"
            "  version: 1.2\n"
            "---\n"
            "body\n"
        )
        s = SkillLoader.from_text(raw)
        assert s.metadata.parent == "y"
        assert s.metadata.version == "1.2"

    def test_quoted_value_with_colon(self):
        raw = '---\nname: x\ndescription: "a: b"\n---\nb\n'
        s = SkillLoader.from_text(raw)
        assert s.description == "a: b"

    def test_unknown_keys_go_to_extra(self):
        raw = "---\nname: x\ncustom: 42\n---\nb\n"
        s = SkillLoader.from_text(raw)
        assert s.metadata.extra.get("custom") == "42"

    def test_no_frontmatter(self):
        raw = "just content\nmore\n"
        s = SkillLoader.from_text(raw)
        assert s.name.startswith("unnamed-")
        assert "just content" in s.content

    def test_unclosed_frontmatter(self):
        raw = "---\nname: x\nbody without close\n"
        with pytest.raises(SkillParseError):
            SkillLoader.from_text(raw)

    def test_invalid_yaml(self):
        raw = "---\nname: [broken\n---\nbody\n"
        with pytest.raises(SkillParseError):
            SkillLoader.from_text(raw)

    def test_non_mapping_frontmatter(self):
        raw = "---\n- just\n- a\n- list\n---\nbody\n"
        with pytest.raises(SkillParseError):
            SkillLoader.from_text(raw)

    def test_bom_is_stripped(self):
        raw = "\ufeff---\nname: x\n---\nbody\n"
        s = SkillLoader.from_text(raw)
        assert s.name == "x"

    def test_empty_description_becomes_empty_string(self):
        raw = "---\nname: x\ndescription:\n---\nb\n"
        s = SkillLoader.from_text(raw)
        assert s.description == ""

    def test_collision_safe_naming(self):
        raw = "---\n---\nb\n"
        b = SkillLoader.from_text(raw)
        c = SkillLoader.from_text(raw)
        assert b.name == c.name
        assert b.name.startswith("unnamed-")


class TestFromDirectory:
    def test_loads_skill_md_files(self, tmp_path: Path):
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("---\nname: a\n---\nbody-a\n")
        sub = skill_dir / "sub"
        sub.mkdir()
        (sub / "SKILL.md").write_text("---\nname: b\n---\nbody-b\n")

        skills = SkillLoader.from_directory(skill_dir)
        names = {s.name for s in skills}
        assert names == {"a", "b"}

    def test_missing_directory_with_ignore(self, tmp_path: Path):
        result = SkillLoader.from_directory(tmp_path / "nope", ignore_errors=True)
        assert result == []

    def test_missing_directory_without_ignore(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            SkillLoader.from_directory(tmp_path / "nope")


class TestSkilledAgentChat:
    def _agent(self, **kw) -> SkilledAgent:
        defaults = {"name": "t", "base_prompt": "you are t", "max_messages": 6}
        defaults.update(kw)
        return SkilledAgent(**defaults)

    def test_no_llm_returns_matched_skills(self):
        a = self._agent()
        a.add_skill_from_text(
            "---\nname: alpha\ndescription: about databases and postgres\n---\nbody\n",
            name="alpha",
        )
        out = a.chat("how do postgres connections work")
        assert "Matched skills: alpha" in out

    def test_no_llm_no_match(self):
        a = self._agent()
        out = a.chat("xyzzy")
        assert "No matching skills" in out

    def test_chat_appends_one_user_one_assistant_per_turn(self):
        seen_lens: list[int] = []

        def llm(msgs):
            seen_lens.append(len(msgs))
            return "ok"

        a = self._agent(llm_fn=llm)
        a.chat("one")
        a.chat("two")
        a.chat("three")

        assert len(a.conversation) == 6
        roles = [m["role"] for m in a.conversation]
        assert roles == ["user", "assistant", "user", "assistant", "user", "assistant"]

    def test_message_cap_is_respected(self):
        def llm(msgs):
            return "ok"

        a = self._agent(llm_fn=llm, max_messages=4)
        for i in range(20):
            a.chat(f"msg-{i}")
        assert len(a.conversation) <= 4

    def test_no_duplicate_system_in_history(self):
        seen_system_count: list[int] = []

        def llm(msgs):
            seen_system_count.append(sum(1 for m in msgs if m["role"] == "system"))
            return "ok"

        a = self._agent(llm_fn=llm, max_messages=10)
        for i in range(5):
            a.chat(f"m-{i}")

        assert all(c == 1 for c in seen_system_count), seen_system_count

    def test_llm_sees_prior_assistant_message(self):
        seen: list[list[dict]] = []

        def llm(msgs):
            seen.append(list(msgs))
            return f"reply-{len(seen)}"

        a = self._agent(llm_fn=llm, max_messages=20)
        a.chat("first")
        a.chat("second")

        assert len(seen[1]) > 2
        assert any(
            m["role"] == "assistant" and "reply-1" in m["content"]
            for m in seen[1]
        )

    def test_reset_conversation_clears_history(self):
        def llm(msgs):
            return "x"

        a = self._agent(llm_fn=llm)
        a.chat("hi")
        assert len(a.conversation) > 0
        a.reset_conversation()
        assert a.conversation == []

    def test_status_reports_counts(self):
        a = self._agent()
        a.add_skill_from_text("---\nname: x\n---\ny\n", name="x")
        s = a.status()
        assert "1 skills" in s
        assert "turns=0/6" in s


class TestSkilledAgentSession:
    def test_start_returns_session(self):
        a = SkilledAgent(name="t")
        sess = a.start_session()
        assert a.session is sess

    def test_start_replaces_existing(self):
        a = SkilledAgent(name="t")
        s1 = a.start_session()
        s1.record_success("topic1", "obs1")
        s2 = a.start_session()
        assert s2 is not s1
        assert s2.discoveries == []

    def test_end_session_returns_path_when_discoveries(self, tmp_path):
        a = SkilledAgent(name="t", learned_dir=str(tmp_path / "learned"))
        sess = a.start_session()
        sess.record_success("topic", "observation", solution="do X")
        path = a.end_session(persist=True)
        assert path is not None
        assert Path(path).exists()

    def test_end_session_none_when_empty(self):
        a = SkilledAgent(name="t")
        a.start_session()
        assert a.end_session(persist=True) is None

    def test_session_scope_persists_on_clean_exit(self, tmp_path):
        a = SkilledAgent(name="t", learned_dir=str(tmp_path / "learned"))
        with a.session_scope() as sess:
            sess.record_success("t", "o")
        assert a.session is None

    def test_session_scope_drops_on_exception(self, tmp_path):
        a = SkilledAgent(name="t", learned_dir=str(tmp_path / "learned"))
        with pytest.raises(RuntimeError):
            with a.session_scope() as sess:
                sess.record_success("t", "o")
                raise RuntimeError("boom")
        assert a.session is None

    def test_session_scope_does_not_persist_on_exception(self, tmp_path):
        a = SkilledAgent(name="t", learned_dir=str(tmp_path / "learned"))
        with pytest.raises(RuntimeError):
            with a.session_scope() as sess:
                sess.record_success("t", "o")
                raise RuntimeError("boom")
        learned = tmp_path / "learned"
        assert not learned.exists() or list(learned.iterdir()) == []


class TestSkilledAgentLoading:
    def test_add_skill_from_text_returns_skill(self):
        a = SkilledAgent(name="t")
        skill = a.add_skill_from_text(
            "---\nname: my-skill\n---\nbody\n", name="my-skill"
        )
        assert isinstance(skill, Skill)
        assert skill.name == "my-skill"
        assert a.registry.get("my-skill") is skill

    def test_add_skill_from_text_no_name(self):
        a = SkilledAgent(name="t")
        skill = a.add_skill_from_text("---\n---\nb\n")
        assert skill.name.startswith("unnamed-")