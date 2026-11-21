"""Prefill time and decode speed of one model at fixed prompt lengths, over any OpenAI-compatible endpoint.

Why: the subagent probe times whole tasks, which mixes model speed with how many steps and how much thinking a
model chooses. This isolates the engine: one cold prompt per length, streamed, so TTFT is prefill and the token
rate after the first token is decode. Every prompt starts with a random nonce, so no prefix cache can serve it.

    python bench-speed.py --url http://<5090>:30000/v1 --model thinkingmachines/Inkling-2-Mini --label inkling
    python bench-speed.py --url http://<5090>:11434/v1 --model qwen3.8:27b --label qwen

In: endpoint, model, lengths in tokens (default 8000,16000,32000), runs per length. Out: one line per run and a JSON
row per run in results/bench-speed-<label>.jsonl. Prompt tokens are the server's own count when it sends usage,
else marked as an estimate (chars/4).
"""
import argparse
import json
import os
import random
import time
import urllib.request

FILLER = ("The ferry left the quay at seven and the gulls followed it out past the breakwater, where the swell "
          "picked up and the deck hands went below to drink coffee and complain about the weather forecast. ")


def build_prompt(n_tokens):
    """A nonce-led haystack of about n_tokens, then a short question.

    Why the nonce: a shared prefix would let the server's prefix cache skip most of the prefill and the number
    would measure the cache, not the engine. In: target tokens (chars/4 estimate). Out: the prompt string.
    """
    nonce = f"Session {random.getrandbits(64):016x}. "
    reps = max(1, (n_tokens * 4) // len(FILLER))
    return nonce + FILLER * reps + "\n\nIn one sentence, what did the deck hands do?"


def run_once(url, model, prompt, max_tokens):
    """Stream one chat completion and time it.

    In: endpoint base (…/v1), model id, prompt, output cap. Out: dict with ttft_s, decode tok/s, output token count
    and the server's prompt token count (None if the server sent no usage). Reasoning and content chunks both count
    as output: the engine generates both.
    """
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens,
                       "temperature": 0, "stream": True, "stream_options": {"include_usage": True}}).encode()
    req = urllib.request.Request(url.rstrip("/") + "/chat/completions", data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    t_first = None
    chunks = 0
    usage = None
    with urllib.request.urlopen(req, timeout=1800) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:") or line == "data: [DONE]":
                continue
            ev = json.loads(line[5:])
            if ev.get("usage"):
                usage = ev["usage"]
            for ch in ev.get("choices") or []:
                d = ch.get("delta") or {}
                if d.get("content") or d.get("reasoning_content") or d.get("reasoning"):
                    chunks += 1
                    if t_first is None:
                        t_first = time.time()
    t_end = time.time()
    out_tokens = (usage or {}).get("completion_tokens") or chunks
    decode = (out_tokens - 1) / (t_end - t_first) if t_first and out_tokens > 1 and t_end > t_first else None
    return {"ttft_s": round(t_first - t0, 2) if t_first else None, "decode_tok_s": round(decode, 1) if decode else None,
            "out_tokens": out_tokens, "prompt_tokens": (usage or {}).get("prompt_tokens"), "wall_s": round(t_end - t0, 1)}


def main():
    """Parse arguments, warm the model once, run every length, write rows."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--lengths", default="8000,16000,32000")
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--max-tokens", type=int, default=512)
    a = ap.parse_args()
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results", f"bench-speed-{a.label}.jsonl")
    run_once(a.url, a.model, "Say OK.", 8)  # warm-up: load and graph capture are not prefill
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for n in [int(x) for x in a.lengths.split(",")]:
        for i in range(a.runs):
            r = run_once(a.url, a.model, build_prompt(n), a.max_tokens)
            r.update({"when": stamp, "label": a.label, "model": a.model, "target_tokens": n, "run": i + 1})
            pt = r["prompt_tokens"] if r["prompt_tokens"] else f"~{n} (est)"
            print(f"  {a.label:8} {n // 1000:>3}k run{i + 1}: prompt={pt} ttft={r['ttft_s']}s "
                  f"decode={r['decode_tok_s']} tok/s out={r['out_tokens']}", flush=True)
            with open(out_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(r) + "\n")


if __name__ == "__main__":
    main()
