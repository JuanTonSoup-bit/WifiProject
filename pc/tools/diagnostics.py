"""
Interactive diagnostics CLI for a running CSI pipeline.

Connects to the control server embedded in `pc/main.py` (default 127.0.0.1:5599)
and lets the user query state, adjust thresholds, and reset the baseline at runtime.

Usage:
    python -m pc.tools.diagnostics            # interactive REPL
    python -m pc.tools.diagnostics status     # one-shot command
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Optional

from pc.tools.control_server import send_command

HELP_TEXT = """
Available commands:
  status              Show current pipeline state and counters
  stats               Detailed per-stage statistics (JSON)
  threshold           Show current detection threshold
  threshold <value>   Set static motion threshold (e.g. `threshold 0.30`)
  reset               Reset DSP baseline and detection state machine
  help                Show this message
  quit | exit         Exit the diagnostics CLI
"""


def _format_status(data: dict) -> str:
    state = data.get("state", "UNKNOWN")
    score = data.get("score", 0.0)
    threshold = data.get("threshold", 0.0)
    occ = data.get("occupancy", "UNKNOWN")
    hz = data.get("ingestion_hz", 0.0)
    drops = data.get("ingestion_drops", 0)
    return (
        f"  state     : {state}\n"
        f"  occupancy : {occ}\n"
        f"  score     : {score:.4f}\n"
        f"  threshold : {threshold:.4f}\n"
        f"  rx rate   : {hz:.1f} Hz   drops: {drops}"
    )


def run_command(host: str, port: int, cmd: str, *args: str) -> int:
    if cmd in ("quit", "exit"):
        return -1
    if cmd in ("help", "?", ""):
        print(HELP_TEXT)
        return 0

    try:
        if cmd == "threshold" and args:
            try:
                value = float(args[0])
            except ValueError:
                print(f"  Invalid threshold value: {args[0]}", file=sys.stderr)
                return 1
            resp = send_command(host, port, "threshold", value=value)
        else:
            resp = send_command(host, port, cmd)
    except (ConnectionRefusedError, ConnectionResetError, OSError) as exc:
        print(f"  Cannot reach control server at {host}:{port} — is pc.main running? ({exc})",
              file=sys.stderr)
        return 1

    if not resp.get("ok"):
        print(f"  ERROR: {resp.get('error')}", file=sys.stderr)
        return 1

    data = resp.get("data", {})
    if cmd == "status":
        print(_format_status(data))
    else:
        print(json.dumps(data, indent=2))
    return 0


def repl(host: str, port: int) -> int:
    print("WiFi CSI Diagnostics CLI — type 'help' for commands, 'quit' to exit.")
    while True:
        try:
            line = input("csi> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        parts = line.split()
        result = run_command(host, port, parts[0], *parts[1:])
        if result == -1:
            break
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="WiFi CSI diagnostics CLI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5599)
    parser.add_argument("command", nargs="*", help="Optional one-shot command")
    args = parser.parse_args()

    if args.command:
        return run_command(args.host, args.port, args.command[0], *args.command[1:])
    return repl(args.host, args.port)


if __name__ == "__main__":
    sys.exit(main())
