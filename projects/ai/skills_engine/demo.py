#!/usr/bin/env python3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SKILLS_PKG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILLS_PKG))

from skills_engine import SkilledAgent, SkillRegistry

SKILLS_DIR = PROJECT_ROOT / ".agents" / "skills"


def demo():
    agent = SkilledAgent(
        name="freelance-assistant",
        base_prompt="You are a helpful freelance developer assistant.",
    )

    print(f"Loading skills from {SKILLS_DIR}")
    count = len(agent.add_skill_from_directory(str(SKILLS_DIR)))
    print(f"Loaded {count} skills\n")

    print(agent.status())
    print()
    print(agent.registry.summary())
    print()

    queries = [
        "how do I set up Neon postgres with branching?",
        "I need to add authentication to my app",
        "how can I store files and images for my users?",
        "I want to call an LLM from my application",
        "how do I deploy a serverless function?",
    ]

    for q in queries:
        print(f"Q: {q}")
        matched = agent.registry.search(q, top_k=2)
        if matched:
            names = ", ".join(s.name for s in matched)
            print(f"  -> Matched: {names}")
        else:
            print("  -> No match")
        print()

    print("=" * 60)
    print("Sample system prompt for query:")
    q = "how do I add auth with Neon?"
    print(f"  '{q}'")
    print("=" * 60)
    prompt = agent.formatter.as_system_prompt(query=q, max_skills=2, max_chars=3000)
    print(prompt[:2000])
    print()

    print("=" * 60)
    print("OpenAI-compatible tool definitions:")
    print("=" * 60)
    tools = agent.formatter.as_tool_definitions()
    for t in tools[:3]:
        fn = t["function"]
        print(f"  {fn['name']}: {fn['description'][:80]}")
    print(f"  ... ({len(tools)} total)")
    print()

    print("=" * 60)
    print("Chat without LLM (skill matching only):")
    print("=" * 60)
    print(agent.chat("I need object storage for user uploads"))


if __name__ == "__main__":
    demo()
