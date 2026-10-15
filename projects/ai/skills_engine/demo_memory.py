#!/usr/bin/env python3
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SKILLS_PKG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILLS_PKG))

from skills_engine import SkilledAgent, SkillRegistry, SessionMemory, SkillPersister

SKILLS_DIR = PROJECT_ROOT / ".agents" / "skills"
LEARNED_DIR = SKILLS_DIR / "learned"


def demo_persistence():
    print("=" * 60)
    print("SESSION MEMORY & PERSISTENCE DEMO")
    print("=" * 60)
    print()

    learned_dir = tempfile.mkdtemp(prefix="skills_learned_")
    print(f"Learned skills dir: {learned_dir}")
    print()

    agent = SkilledAgent(
        name="demo-agent",
        base_prompt="You are a helpful freelance developer assistant.",
        learned_dir=learned_dir,
    )
    count = len(agent.add_skill_from_directory(str(SKILLS_DIR)))
    print(f"Loaded {count} base skills + learned skills from {learned_dir}")
    print(agent.status())
    print()

    print("--- Session 1: Agent discovers CF solving patterns ---")
    session1 = agent.start_session()
    session1.record_failure(
        "cloudflare-managed-turnstile",
        "Oxylabs auto-solver fails on CF 'managed' turnstile — 3 attempts all fail",
        workaround="Use CapSolver API as fallback, or schedule runs at different hours",
        tags=["cloudflare", "captcha", "scraping", "skelbiu"],
    )
    session1.record_success(
        "stealthy-engine-no-cf",
        "When CF challenge is not active, stealthy engine gets 200 OK with no delay",
        solution="Schedule multiple runs per day to catch CF-down windows",
        tags=["cloudflare", "scheduling", "scraping"],
    )
    session1.record_pattern(
        "cf-intermittent",
        "CF 'managed' turnstile is intermittent — success at 12 UTC, failures at 07, 14-16 UTC",
        tags=["cloudflare", "timing", "skelbiu"],
    )
    print(f"  Recorded {len(session1.discoveries)} discoveries")
    path1 = agent.end_session(persist=True)
    print(f"  Persisted as: {path1}")
    print()

    print("--- Session 2: New agent inherits session 1's knowledge ---")
    agent2 = SkilledAgent(
        name="new-agent",
        base_prompt="You are a new agent with inherited knowledge.",
        learned_dir=learned_dir,
    )
    agent2.add_skill_from_directory(str(SKILLS_DIR))
    print(agent2.status())
    print()

    print("Query: 'how do I handle Cloudflare when scraping?'")
    matched = agent2.registry.search("Cloudflare captcha scraping", top_k=3)
    for m in matched:
        print(f"  -> {m.name} (source={m.source_type}): {m.description[:80]}")
    print()

    context = agent2.registry.resolve_context("Cloudflare scraping", max_skills=2, max_chars=2000)
    print("Injected context preview:")
    print(context[:600])
    print()

    print("--- Agent 2 also records learnings ---")
    session2 = agent2.start_session()
    session2.record_success(
        "capsolver-fallback",
        "CapSolver API successfully solves managed turnstile when auto-solve fails",
        solution="Configure CAPSOLVER_API_KEY env var, fallback kicks in automatically",
        tags=["captcha", "capsolver", "scraping"],
    )
    path2 = agent2.end_session(persist=True)
    print(f"  Persisted: {path2}")
    print()

    print("--- Verify persisted skills on disk ---")
    learned = Path(learned_dir)
    for skill_md in sorted(learned.rglob("SKILL.md")):
        print(f"  {skill_md.relative_to(learned)}")
        content = skill_md.read_text()
        for line in content.split("\n")[:5]:
            print(f"    {line}")
        print()

    print("=" * 60)
    print("PERSISTENCE DEMO COMPLETE")
    print("=" * 60)
    print(f"Learned skills stored in: {learned_dir}")
    print(f"Any future agent with learned_dir='{learned_dir}' inherits these.")


if __name__ == "__main__":
    demo_persistence()
