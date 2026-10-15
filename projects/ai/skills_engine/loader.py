from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Optional, Union

import requests
import yaml

from .models import Skill, SkillMetadata


_log = logging.getLogger(__name__)


class SkillParseError(Exception):
    """Raised when a skill file cannot be parsed."""


class SkillLoader:
    FRONTMATTER_RE = None

    @classmethod
    def from_file(cls, path: Union[str, Path]) -> Skill:
        path = Path(path)
        raw = path.read_text(encoding="utf-8")
        skill = cls.from_text(raw, source_path=str(path.resolve()))
        skill.source_type = "local"
        skill.hash = hashlib.sha256(raw.encode()).hexdigest()
        return skill

    @classmethod
    def from_text(cls, raw: str, *, source_path: Optional[str] = None) -> Skill:
        name, description, metadata, content = cls._parse(raw)
        if not name:
            name = f"unnamed-{hashlib.sha256(raw.encode()).hexdigest()[:8]}"
        skill = Skill(
            name=name,
            description=description,
            content=content,
            metadata=metadata,
        )
        if source_path is not None:
            skill.source_path = source_path
        return skill

    @classmethod
    def from_directory(
        cls,
        directory: Union[str, Path],
        *,
        ignore_errors: bool = False,
    ) -> list[Skill]:
        directory = Path(directory)
        if not directory.exists():
            if ignore_errors:
                return []
            raise FileNotFoundError(f"skills directory not found: {directory}")

        skills: list[Skill] = []
        errors: list[tuple[Path, Exception]] = []

        for md_file in sorted(directory.rglob("SKILL.md")):
            try:
                skills.append(cls.from_file(md_file))
            except Exception as e:
                errors.append((md_file, e))
        for md_file in sorted(directory.rglob("*.skill.md")):
            try:
                skills.append(cls.from_file(md_file))
            except Exception as e:
                errors.append((md_file, e))

        if errors:
            for path, err in errors:
                _log.warning("skip %s: %s", path, err)
            if not ignore_errors and not skills:
                raise SkillParseError(
                    f"all {len(errors)} skill file(s) failed to parse in {directory}"
                )

        return skills

    @classmethod
    def from_url(cls, url: str, name: Optional[str] = None) -> Skill:
        resp = requests.get(url, timeout=30, stream=True)
        resp.raise_for_status()
        raw = resp.text
        skill = cls.from_text(raw, source_path=url)
        skill.source_type = "url"
        skill.hash = hashlib.sha256(raw.encode()).hexdigest()
        if name:
            skill.name = name
        return skill

    @classmethod
    def from_github(cls, repo: str, skill_path: str, ref: str = "main") -> Skill:
        url = f"https://raw.githubusercontent.com/{repo}/{ref}/{skill_path}"
        skill = cls.from_url(url)
        skill.source_type = "github"
        skill.source_path = f"github:{repo}/{skill_path}@{ref}"
        skill.metadata.source = f"https://github.com/{repo}/tree/{ref}/{skill_path}"
        return skill

    @classmethod
    def _parse(cls, raw: str) -> tuple[Optional[str], str, SkillMetadata, str]:
        raw = raw.lstrip("\ufeff")
        if not raw.startswith("---"):
            return None, "", SkillMetadata(), raw.strip()

        lines = raw.split("\n")
        if not lines or lines[0].strip() != "---":
            return None, "", SkillMetadata(), raw.strip()

        end_idx = None
        for i in range(1, len(lines)):
            if lines[i].rstrip() == "---":
                end_idx = i
                break
        if end_idx is None:
            raise SkillParseError("frontmatter opened with '---' but never closed")

        fm_text = "\n".join(lines[1:end_idx])
        content = "\n".join(lines[end_idx + 1:]).strip()

        try:
            parsed = yaml.safe_load(fm_text) or {}
        except yaml.YAMLError as e:
            raise SkillParseError(f"invalid frontmatter YAML: {e}") from e

        if not isinstance(parsed, dict):
            raise SkillParseError(
                f"frontmatter must be a mapping, got {type(parsed).__name__}"
            )

        name = parsed.pop("name", None)
        if name is not None and not isinstance(name, str):
            name = str(name)
        description = parsed.pop("description", "") or ""
        if isinstance(description, str):
            description = description.strip()
        else:
            description = str(description)

        known = {"parent", "source", "version", "tags", "metadata"}
        meta_kwargs: dict = {}
        extra: dict[str, str] = {}
        for k, v in parsed.items():
            if k == "metadata" and isinstance(v, dict):
                for mk, mv in v.items():
                    if mk in {"parent", "source", "version", "tags"}:
                        meta_kwargs[mk] = mv if mk != "version" or isinstance(mv, str) else str(mv)
                    else:
                        extra[mk] = "" if mv is None else str(mv)
            elif k in known:
                meta_kwargs[k] = v if k != "version" or isinstance(v, str) else str(v)
            else:
                extra[k] = "" if v is None else str(v)
        if extra:
            meta_kwargs["extra"] = extra

        return name, description, SkillMetadata(**meta_kwargs), content