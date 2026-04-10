"""Smoke test: call Anthropic through the proxy."""
from __future__ import annotations

import argparse
import json
import sys
import time

import httpx


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--proxy", default="http://127.0.0.1:8000")
    p.add_argument("--key", required=True)
    p.add_argument("--model", default="claude-haiku-4-5-20251001")
    p.add_argument("--prompt", default="Say 'hello from anthropic' in one short sentence.")
    p.add_argument("--stream", action="store_true")
    args = p.parse_args()

    url = f"{args.proxy}/anthropic/v1/messages"
    payload = {
        "model": args.model,
        "max_tokens": 128,
        "messages": [{"role": "user", "content": args.prompt}],
        "stream": args.stream,
    }
    headers = {"Authorization": f"Bearer {args.key}", "Content-Type": "application/json"}

    t0 = time.time()
    with httpx.Client(timeout=60.0) as client:
        if args.stream:
            first = None
            text = ""
            with client.stream("POST", url, json=payload, headers=headers) as r:
                r.raise_for_status()
                for line in r.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data:
                        continue
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    # Anthropic streaming events
                    if obj.get("type") == "content_block_delta":
                        piece = obj.get("delta", {}).get("text", "")
                        if piece:
                            if first is None:
                                first = time.time()
                            text += piece
                            sys.stdout.write(piece)
                            sys.stdout.flush()
            print()
            print(f"[anthropic] streaming ttft={first - t0:.2f}s total={time.time() - t0:.2f}s chars={len(text)}")
        else:
            r = client.post(url, json=payload, headers=headers)
            r.raise_for_status()
            data = r.json()
            content = data["content"][0]["text"]
            print(content)
            print(f"[anthropic] non-stream total={time.time() - t0:.2f}s chars={len(content)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
