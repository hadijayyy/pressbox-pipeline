#!/usr/bin/env python3
"""
AI Agent Pipeline Orchestrator
==============================
Runs a sequence of AI agents, each with its own prompt.
Output of one agent becomes input of the next.

Usage:
    python3 orchestrator.py                     # Run all agents
    python3 orchestrator.py --from 2 --to 4     # Run agents 2-4 only
    python3 orchestrator.py --dry-run            # Show what would run
    python3 orchestrator.py --status             # Check output files

Structure:
    prompts/
    ├── 1-scrape.md     Agent 1 prompt
    ├── 2-enrich.md     Agent 2 prompt
    ├── 3-write.md      Agent 3 prompt
    └── 4-publish.md    Agent 4 prompt

    output/
    ├── 1-scrape.json   Agent 1 output
    ├── 2-enrich.json   Agent 2 output
    ├── 3-write.json    Agent 3 output
    └── 4-publish.json  Agent 4 output
"""

import os
import sys
import json
import glob
import argparse
import subprocess
from pathlib import Path
from datetime import datetime


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).parent
PROMPTS_DIR = BASE_DIR / "prompts"
OUTPUT_DIR = BASE_DIR / "output"

# Ensure dirs exist
PROMPTS_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# Agent Runner
# ---------------------------------------------------------------------------

def load_prompt(step: int) -> str:
    """Load prompt markdown for a given step."""
    pattern = PROMPTS_DIR / f"{step}-*.md"
    files = sorted(glob.glob(str(pattern)))
    if not files:
        raise FileNotFoundError(f"No prompt found for step {step}: {pattern}")
    with open(files[0], "r", encoding="utf-8") as f:
        return f.read()


def load_input(step: int) -> str | None:
    """Load output from previous step as input."""
    prev = step - 1
    prev_file = OUTPUT_DIR / f"{prev}-*.json"
    files = sorted(glob.glob(str(prev_file)))
    if not files:
        return None
    with open(files[0], "r", encoding="utf-8") as f:
        return f.read()


def save_output(step: int, data: str):
    """Save agent output to JSON file."""
    # Find the agent name from prompt filename
    pattern = PROMPTS_DIR / f"{step}-*.md"
    files = sorted(glob.glob(str(pattern)))
    if files:
        name = Path(files[0]).stem  # e.g. "1-scrape"
    else:
        name = f"{step}-output"

    output_file = OUTPUT_DIR / f"{name}.json"
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(data)
    return output_file


def run_agent(step: int, prompt: str, input_data: str | None = None) -> str:
    """
    Run a single AI agent with its prompt.

    In production, this calls an LLM API (OpenAI, Anthropic, local model).
    Here we demonstrate the flow with a placeholder that constructs
    the full prompt and would return LLM output.

    To integrate with real LLM, replace this function with:
        - OpenAI API call
        - Anthropic API call
        - Hermes agent call
        - Local model inference
    """
    # Build the full prompt
    full_prompt = prompt
    if input_data:
        full_prompt += f"\n\n---\n\n## Input Data\n\n```json\n{input_data}\n```"

    # -----------------------------------------------------------------------
    # REAL LLM CALL (uncomment one):
    # -----------------------------------------------------------------------

    # Option A: OpenAI
    # import openai
    # client = openai.OpenAI()
    # response = client.chat.completions.create(
    #     model="gpt-4o",
    #     messages=[{"role": "user", "content": full_prompt}],
    #     temperature=0.7,
    # )
    # return response.choices[0].message.content

    # Option B: Anthropic
    # import anthropic
    # client = anthropic.Anthropic()
    # response = client.messages.create(
    #     model="claude-sonnet-4-20250514",
    #     max_tokens=4096,
    #     messages=[{"role": "user", "content": full_prompt}],
    # )
    # return response.content[0].text

    # Option C: Hermes Agent (subagent)
    # result = subprocess.run(
    #     ["hermes", "run", "--prompt", full_prompt],
    #     capture_output=True, text=True, timeout=300
    # )
    # return result.stdout

    # -----------------------------------------------------------------------
    # DEMO MODE (returns placeholder — replace with real LLM call above)
    # -----------------------------------------------------------------------
    demo_output = {
        1: '[{"headline": "Demo headline", "url": "https://example.com", "image_url": "https://example.com/img.jpg", "category": "football"}]',
        2: '[{"headline": "Demo headline", "url": "https://example.com", "image_url": "https://example.com/img.jpg", "category": "football", "summary": "Demo summary", "key_people": ["Player"], "sentiment": "neutral", "engagement_score": 7, "topics": ["football"]}]',
        3: '[{"headline": "Demo headline", "url": "https://example.com", "image_url": "https://example.com/img.jpg", "post_text": "Demo post caption", "hashtags": ["#football"], "engagement_score": 7, "status": "ready"}]',
        4: '[{"headline": "Demo headline", "url": "https://example.com", "platform": "threads", "post_url": "https://threads.net/post/123", "status": "published", "published_at": "2026-06-17T19:00:00Z", "error": null}]',
    }
    return demo_output.get(step, "{}")


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def get_agent_steps() -> list[dict]:
    """Discover all agent steps from prompt files."""
    steps = []
    for f in sorted(glob.glob(str(PROMPTS_DIR / "*.md"))):
        name = Path(f).stem  # e.g. "1-scrape"
        step_num = int(name.split("-")[0])
        steps.append({"step": step_num, "name": name, "prompt_file": f})
    return steps


def run_pipeline(from_step: int = 1, to_step: int = 99,
                 dry_run: bool = False) -> list[dict]:
    """Run the full pipeline or a subset of steps."""
    steps = get_agent_steps()
    results = []

    print(f"\n{'='*60}")
    print(f" AI AGENT PIPELINE")
    print(f" {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n")

    for agent in steps:
        step = agent["step"]

        # Filter by range
        if step < from_step or step > to_step:
            continue

        print(f"{'─'*60}")
        print(f"▶ Agent {step}: {agent['name']}")
        print(f"  Prompt: {agent['prompt_file']}")

        if dry_run:
            print(f"  Status: DRY RUN (skipped)")
            results.append({"step": step, "status": "dry_run"})
            continue

        try:
            # Load prompt
            prompt = load_prompt(step)
            print(f"  Prompt loaded: {len(prompt)} chars")

            # Load input from previous step
            input_data = load_input(step)
            if input_data:
                print(f"  Input: {len(input_data)} chars (from step {step-1})")
            else:
                print(f"  Input: none (first step)")

            # Run agent
            print(f"  Running agent...")
            output = run_agent(step, prompt, input_data)

            # Save output
            output_file = save_output(step, output)
            print(f"  Output: {output_file}")
            print(f"  Result: {len(output)} chars")

            results.append({
                "step": step,
                "status": "success",
                "output_file": str(output_file),
                "output_size": len(output),
            })

        except Exception as e:
            print(f"  ❌ Error: {e}")
            results.append({
                "step": step,
                "status": "failed",
                "error": str(e),
            })

    # Summary
    print(f"\n{'='*60}")
    print(f" SUMMARY")
    print(f"{'='*60}")
    success = sum(1 for r in results if r["status"] == "success")
    failed = sum(1 for r in results if r["status"] == "failed")
    print(f"  ✅ Success: {success}")
    print(f"  ❌ Failed:  {failed}")
    print(f"  ⏭️  Skipped: {len(results) - success - failed}")
    print()

    return results


# ---------------------------------------------------------------------------
# Status Check
# ---------------------------------------------------------------------------

def show_status():
    """Show status of all output files."""
    print(f"\n{'='*60}")
    print(f" PIPELINE STATUS")
    print(f"{'='*60}\n")

    steps = get_agent_steps()
    for agent in steps:
        step = agent["step"]
        pattern = OUTPUT_DIR / f"{step}-*.json"
        files = sorted(glob.glob(str(pattern)))

        if files:
            f = files[0]
            size = os.path.getsize(f)
            mtime = datetime.fromtimestamp(os.path.getmtime(f))
            try:
                with open(f) as fh:
                    data = json.load(fh)
                count = len(data) if isinstance(data, list) else 1
            except:
                count = "?"
            print(f"  ✅ Step {step}: {agent['name']}")
            print(f"     File: {f}")
            print(f"     Size: {size/1024:.1f} KB | Records: {count}")
            print(f"     Updated: {mtime.strftime('%Y-%m-%d %H:%M:%S')}")
        else:
            print(f"  ⏳ Step {step}: {agent['name']}")
            print(f"     No output yet")
        print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="AI Agent Pipeline Orchestrator")
    parser.add_argument("--from-step", type=int, default=1, dest="from_step",
                        help="Start from this step")
    parser.add_argument("--to-step", type=int, default=99, dest="to_step",
                        help="End at this step")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would run without executing")
    parser.add_argument("--status", action="store_true",
                        help="Check output files status")
    args = parser.parse_args()

    if args.status:
        show_status()
    else:
        run_pipeline(from_step=args.from_step, to_step=args.to_step,
                     dry_run=args.dry_run)


if __name__ == "__main__":
    main()
