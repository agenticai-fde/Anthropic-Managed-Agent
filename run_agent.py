"""Chat with the Healthcare Managed Agent created by setup_agent.py.

Each run starts a new session (a fresh cloud container). CSV files in data/ are
uploaded and mounted at /mnt/session/uploads/data/ so the agent can analyse them.

    python run_agent.py                                  # interactive chat
    python run_agent.py -q "Summarise the cohort by diagnosis"
    python run_agent.py --data path\\to\\other.csv        # analyse a different file
"""
import argparse
import json
import sys
import time
from pathlib import Path

import anthropic
from dotenv import load_dotenv

HERE = Path(__file__).parent
load_dotenv(HERE / ".env")

CONFIG_FILE = HERE / "agent_config.json"
DATA_DIR = HERE / "data"
OUTPUT_DIR = HERE / "outputs"
MOUNT_DIR = "/data"  # session files land under /mnt/session/uploads, so this is /mnt/session/uploads/data

EXAMPLE_QUESTIONS = [
    "Give me a cohort overview: patients by diagnosis, average age and length of stay.",
    "Which diagnoses have the highest 30-day readmission rate?",
    "List diabetic patients with HbA1c >= 9% and explain why that matters.",
    "Chart average length of stay by diagnosis and save it as a PNG.",
    "Write a one-page quality report as report.md with your key findings.",
]


def upload_data(client: anthropic.Anthropic, paths: list[Path]) -> list[dict]:
    """Upload local data files and return session resources that mount them."""
    resources = []
    for path in paths:
        with open(path, "rb") as f:
            uploaded = client.beta.files.upload(file=f)
        resources.append({
            "type": "file",
            "file_id": uploaded.id,
            "mount_path": f"{MOUNT_DIR}/{path.name}",
        })
        print(f"Mounted {path.name} -> /mnt/session/uploads{MOUNT_DIR}/{path.name}")
    return resources


def approve(event) -> bool:
    """Ask the user about a tool call that the 'auto' policy paused for review."""
    print(f"\n[approval needed] {event.name}: {json.dumps(event.input)[:500]}")
    return input("Allow this tool call? [y/N] ").strip().lower() == "y"


def run_turn(client: anthropic.Anthropic, session_id: str, text: str) -> None:
    """Send one message and stream events until the agent is done with it."""
    with client.beta.sessions.events.stream(session_id=session_id) as stream:
        # Stream-first: open the stream, then send, so no events are missed.
        client.beta.sessions.events.send(
            session_id=session_id,
            events=[{"type": "user.message", "content": [{"type": "text", "text": text}]}],
        )
        for event in stream:
            if event.type == "agent.message":
                for block in event.content:
                    if block.type == "text":
                        print(block.text, end="", flush=True)
            elif event.type in ("agent.tool_use", "agent.mcp_tool_use"):
                if event.evaluated_permission == "ask":
                    allowed = approve(event)
                    client.beta.sessions.events.send(
                        session_id=session_id,
                        events=[{
                            "type": "user.tool_confirmation",
                            "tool_use_id": event.id,
                            "result": "allow" if allowed else "deny",
                        }],
                    )
                else:
                    print(f"\n  [tool] {event.name}", flush=True)
            elif event.type == "session.status_idle":
                if event.stop_reason.type != "requires_action":
                    print()
                    return
            elif event.type == "session.status_terminated":
                print("\n[session terminated]")
                return


def download_outputs(client: anthropic.Anthropic, session_id: str) -> None:
    """Save any files the agent wrote to /mnt/session/outputs/."""
    for _ in range(3):  # files appear a few seconds after the agent goes idle
        files = client.beta.files.list(scope_id=session_id, betas=["managed-agents-2026-04-01"])
        if files.data:
            break
        time.sleep(2)
    else:
        return
    OUTPUT_DIR.mkdir(exist_ok=True)
    for f in files.data:
        try:
            client.beta.files.download(f.id).write_to_file(OUTPUT_DIR / f.filename)
        except anthropic.BadRequestError:
            continue  # input files we uploaded are listed too but can't be downloaded
        print(f"Downloaded {f.filename} -> outputs/")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("-q", "--question", help="send one message and exit")
    parser.add_argument("--data", type=Path, nargs="*",
                        help="data files to mount (default: all CSVs in data/)")
    args = parser.parse_args()

    if not CONFIG_FILE.exists():
        sys.exit("agent_config.json not found. Run: python setup_agent.py")
    config = json.loads(CONFIG_FILE.read_text())

    data_files = args.data or sorted(DATA_DIR.glob("*.csv"))
    missing = [p for p in data_files if not p.exists()]
    if missing:
        sys.exit(f"Not found: {', '.join(map(str, missing))}")

    client = anthropic.Anthropic()
    session = client.beta.sessions.create(
        agent={"type": "agent", "id": config["agent_id"], "version": config["agent_version"]},
        environment_id=config["environment_id"],
        title="Healthcare data session",
        resources=upload_data(client, data_files),
    )
    print(f"Session {session.id}")
    print(f"Trace: https://platform.claude.com/workspaces/default/sessions/{session.id}\n")

    try:
        if args.question:
            run_turn(client, session.id, args.question)
        else:
            print("Healthcare Data Assistant. Example questions:")
            for q in EXAMPLE_QUESTIONS:
                print(f"  - {q}")
            print("\nNot for diagnosis or treatment decisions. Use de-identified data only.")
            print("Empty line or 'exit' to quit.")
            while True:
                text = input("\nYou> ").strip()
                if not text or text.lower() in {"exit", "quit"}:
                    break
                print("Agent> ", end="")
                run_turn(client, session.id, text)
        download_outputs(client, session.id)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
