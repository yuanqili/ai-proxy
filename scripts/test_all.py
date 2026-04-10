"""Run all three provider smoke tests in sequence.

Usage:
    uv run python scripts/test_all.py --key sk-aiprx-xxx [--stream]
"""
from __future__ import annotations

import argparse
import subprocess
import sys


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--proxy", default="http://127.0.0.1:8000")
    p.add_argument("--key", required=True)
    p.add_argument("--stream", action="store_true")
    args = p.parse_args()

    scripts = [
        ("OpenAI",     ["python", "scripts/test_openai.py"]),
        ("Anthropic",  ["python", "scripts/test_anthropic.py"]),
        ("OpenRouter", ["python", "scripts/test_openrouter.py"]),
    ]
    results: list[tuple[str, int]] = []
    for label, cmd in scripts:
        print(f"\n========== {label} ==========")
        full = cmd + ["--proxy", args.proxy, "--key", args.key]
        if args.stream:
            full.append("--stream")
        rc = subprocess.call(full)
        results.append((label, rc))

    print("\n========== Summary ==========")
    for label, rc in results:
        status = "✓" if rc == 0 else f"✗ (rc={rc})"
        print(f"  {status} {label}")
    return 0 if all(rc == 0 for _, rc in results) else 1


if __name__ == "__main__":
    sys.exit(main())
