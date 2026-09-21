#!/usr/bin/env python3
"""Hard release gate: success means a final guide handoff is allowed."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from _build_state import evaluate, print_state, save


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("workbench", type=Path)
    args = parser.parse_args()
    state = evaluate(args.workbench, run_gates=True)
    save(args.workbench, state)
    print_state(state)
    dimensions = state.get("audit_dimensions", {})
    for name in ("structure", "research", "media"):
        print(f"{name.upper()} {'PASS' if dimensions.get(name) is True else 'FAIL'}")
    handbook_state_ok = state.get("checks", {}).get("trip_decisions_recorded") is True
    print(f"HANDBOOK-STATE {'PASS' if handbook_state_ok else 'FAIL'}")
    if not state["handoff_allowed"]:
        print("HANDOFF BLOCKED: perform the printed next action; do not send a completion final response.")
        return 3
    cache = Path(__file__).with_name("plan_incremental_validation.py")
    result = subprocess.run([sys.executable, str(cache), str(args.workbench.resolve()), "--write"])
    if result.returncode:
        print("HANDOFF BLOCKED: incremental validation cache could not be recorded.")
        return result.returncode
    print("HANDOFF ALLOWED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
