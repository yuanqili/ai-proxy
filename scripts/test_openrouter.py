"""Smoke test: call OpenRouter through the proxy."""
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
    p.add_argument("--model", default="openai/gpt-4o-mini")
    p.add_argument("--prompt", default="Say 'hello from openrouter' in one short sentence.")
    p.add_argument("--stream", action="store_true")
    args = p.parse_args()

    url = f"{args.proxy}/openrouter/chat/completions"
    payload = {
        "model": args.model,
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
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    delta = obj.get("choices", [{}])[0].get("delta", {}).get("content")
                    if delta:
                        if first is None:
                            first = time.time()
                        text += delta
                        sys.stdout.write(delta)
                        sys.stdout.flush()
            print()
            print(f"[openrouter] streaming ttft={first - t0:.2f}s total={time.time() - t0:.2f}s chars={len(text)}")
        else:
            r = client.post(url, json=payload, headers=headers)
            r.raise_for_status()
            data = r.json()
            content = data["choices"][0]["message"]["content"]
            print(content)
            print(f"[openrouter] non-stream total={time.time() - t0:.2f}s chars={len(content)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
