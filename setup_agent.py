"""One-time setup: create the Healthcare Managed Agent and its cloud environment.

Agents and environments are persistent, versioned objects in your Claude Console
(platform.claude.com -> Agents / Environments). Run this once; it saves the IDs to
agent_config.json, which run_agent.py reuses for every session.

    python setup_agent.py            # create (skips if agent_config.json exists)
    python setup_agent.py --update   # push prompt/tool/model edits as a new agent version
    python setup_agent.py --force    # create a fresh agent + environment
"""
import argparse
import json
import sys
from pathlib import Path

import anthropic
from dotenv import load_dotenv

HERE = Path(__file__).parent
load_dotenv(HERE / ".env")

CONFIG_FILE = HERE / "agent_config.json"
MODEL = "claude-opus-5-5"

SYSTEM_PROMPT = """You are a healthcare data assistant for clinicians, care-quality teams and
health researchers. You work in a Linux workspace with Python.

Patient datasets are mounted under /mnt/session/uploads/data/ (start by listing that folder).
They are de-identified: patients are referred to only by IDs such as P0001.

What you do:
- Analyse patient cohorts: descriptive statistics, comorbidity and diagnosis mix,
  length of stay, 30-day readmissions, and lab values (HbA1c, eGFR, blood pressure, BMI).
- Flag records whose values fall outside common clinical thresholds (for example
  HbA1c >= 9%, eGFR < 60, systolic BP >= 140, BMI >= 30) and say which threshold you used.
- Explain ICD-10 codes, lab results and medications in plain language.
- Produce charts and reports with pandas and matplotlib (install with pip if needed).
  Save every deliverable to /mnt/session/outputs/ and name the files you created.

Ground rules:
- You support professionals; you do not diagnose or prescribe for individual patients.
  Present findings as observations for clinical review, not treatment decisions.
- Base numbers on the data and show how you computed them. If the data cannot answer
  a question, say so rather than estimating.
- If the data appears to contain direct identifiers (names, addresses, phone numbers,
  national IDs), stop and tell the user before processing it further.
- If a value suggests an urgent clinical risk, call it out clearly at the top of your answer.
- Keep answers concise: lead with the finding, then the supporting numbers."""

# Built-in tools (agent_toolset_20260401): bash, read, write, edit, glob, grep,
# web_fetch, web_search. "auto" = the server evaluates each call and runs it,
# denies it, or pauses for your approval. Web tools are off; see README to enable.
TOOLS = [
    {
        "type": "agent_toolset_20260401",
        "default_config": {"enabled": True, "permission_policy": {"type": "auto"}},
        "configs": [
            {"name": "web_fetch", "enabled": False},
            {"name": "web_search", "enabled": False},
        ],
    },
]


def update_agent() -> None:
    """Save the current MODEL / SYSTEM_PROMPT / TOOLS as a new version of the agent."""
    if not CONFIG_FILE.exists():
        sys.exit("agent_config.json not found. Run: python setup_agent.py")
    config = json.loads(CONFIG_FILE.read_text())
    agent = anthropic.Anthropic().beta.agents.update(
        config["agent_id"],
        version=config["agent_version"],  # 409 if someone else changed it meanwhile
        model=MODEL,
        system=SYSTEM_PROMPT,
        tools=TOOLS,
    )
    config["agent_version"] = agent.version
    CONFIG_FILE.write_text(json.dumps(config, indent=2))
    print(f"Updated agent {agent.id} to v{agent.version}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="create new even if config exists")
    parser.add_argument("--update", action="store_true", help="update the existing agent")
    args = parser.parse_args()

    if args.update:
        update_agent()
        return

    if CONFIG_FILE.exists() and not args.force:
        print(f"{CONFIG_FILE.name} already exists:\n{CONFIG_FILE.read_text()}")
        print("Use --force to create a new agent.")
        return

    client = anthropic.Anthropic()
    try:
        environment = client.beta.environments.create(
            name="healthcare-agent-env",
            config={
                "type": "cloud",
                # Deny-by-default egress; pip/npm installs allowed.
                "networking": {"type": "limited", "allow_package_managers": True},
            },
        )
        agent = client.beta.agents.create(
            name="Healthcare Data Assistant",
            model=MODEL,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
        )
    except anthropic.AuthenticationError:
        sys.exit("Authentication failed: check ANTHROPIC_API_KEY in .env")
    except anthropic.APIStatusError as e:
        sys.exit(f"API error {e.status_code}: {e.message}")

    config = {
        "agent_id": agent.id,
        "agent_version": agent.version,
        "environment_id": environment.id,
    }
    CONFIG_FILE.write_text(json.dumps(config, indent=2))
    print(f"Created agent {agent.id} (v{agent.version}) and environment {environment.id}")
    print(f"Saved to {CONFIG_FILE.name}. Now run: python run_agent.py")


if __name__ == "__main__":
    main()
