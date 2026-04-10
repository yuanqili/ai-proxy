"""Manual end-to-end test: stream a chat completion through the proxy.

Usage:
    python scripts/test_stream.py
    python scripts/test_stream.py --model anthropic/claude-haiku-4-5
    python scripts/test_stream.py --prompt "write a limerick about llamas"
"""

from __future__ import annotations

import argparse
import json
import sys
import time

import httpx


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--proxy", default="http://127.0.0.1:8000")
    p.add_argument("--model", default="google/gemini-2.0-flash-exp:free")
    p.add_argument("--prompt", default="Write a short poem about spring. Take your time.")
    args = p.parse_args()

    url = f"{args.proxy}/openrouter/chat/completions"
    payload = {
        "model": args.model,
        "stream": True,
        "messages": [{"role": "user", "content": args.prompt}],
    }

    print(f"POST {url}")
    print(f"model: {args.model}")
    print(f"prompt: {args.prompt}")
    print("--- streaming ---")

    t0 = time.time()
    first_token_at: float | None = None
    text = ""

    with httpx.Client(timeout=120.0) as client:
        with client.stream("POST", url, json=payload) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                delta = obj.get("choices", [{}])[0].get("delta", {}).get("content")
                if delta:
                    if first_token_at is None:
                        first_token_at = time.time()
                    text += delta
                    sys.stdout.write(delta)
                    sys.stdout.flush()

    print("\n--- done ---")
    elapsed = time.time() - t0
    ttft = (first_token_at - t0) if first_token_at else None
    print(f"total: {elapsed:.2f}s")
    if ttft is not None:
        print(f"ttft:  {ttft:.2f}s")
    print(f"chars: {len(text)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
