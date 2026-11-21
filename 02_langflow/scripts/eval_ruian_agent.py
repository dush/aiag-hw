"""Evaluate the ruian-agent flow on tests/ruian_agent_eval.json through the Langflow API.

Every run uses a new session, so the Agent's chat memory does not carry answers between questions.
Answers are normalized (lowercase, no diacritics, '110 00' -> '11000', '50,08' -> '50.08') and must
match every `expect` regex and no `forbid` regex. Rate limits (429) and other errors are counted
separately from wrong answers, because free models are rate limited.

Usage (from 02_langflow, Langflow running, LANGFLOW_API_KEY in .env):
    uv run --env-file .env python scripts/eval_ruian_agent.py [--repeat 3] [--tags baseline] [--only id,id]
        [--out results.json]
"""

import argparse
import json
import os
import re
import statistics
import sys
import time
import unicodedata
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QUESTIONS = ROOT / "tests" / "ruian_agent_eval.json"
FLOW_ID = "c3a91e4d-6f2b-4d8e-b1a7-9e5d2f0c7a34"


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"(?<=\d)[   ](?=\d)", "", text)  # 110 00 -> 11000, 2 860 -> 2860
    text = re.sub(r"(?<=\d),(?=\d)", ".", text)  # decimal comma
    return re.sub(r"\s+", " ", text)


def run_flow(url: str, api_key: str, flow_id: str, question: str, timeout: int) -> dict:
    body = json.dumps({
        "input_value": question, "input_type": "chat", "output_type": "chat", "session_id": str(uuid.uuid4()),
    }).encode()
    req = urllib.request.Request(
        f"{url}/api/v1/run/{flow_id}?stream=false", data=body, method="POST",
        headers={"Content-Type": "application/json", "x-api-key": api_key},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def answer_and_tools(result: dict) -> tuple[str, list[str]]:
    message = result["outputs"][0]["outputs"][0]["results"]["message"]
    data = message.get("data") or message
    text = data.get("text") or message.get("text") or ""
    tools = [
        c.get("name", "?")
        for block in data.get("content_blocks") or []
        for c in block.get("contents") or []
        if c.get("type") == "tool_use"
    ]
    return text, tools


def classify_error(text: str) -> str:
    return "rate_limited" if re.search(r"\b429\b|rate.?limit", text, re.I) else "error"


def evaluate(q: dict, args, api_key: str) -> dict:
    start = time.monotonic()
    try:
        text, tools = answer_and_tools(run_flow(args.url, api_key, args.flow_id, q["question"], args.timeout))
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:500]
        return {"status": classify_error(detail), "detail": f"HTTP {e.code}: {detail}", "seconds": time.monotonic() - start}
    except (urllib.error.URLError, TimeoutError, KeyError, IndexError, ValueError) as e:
        return {"status": "error", "detail": f"{type(e).__name__}: {e}", "seconds": time.monotonic() - start}
    seconds = time.monotonic() - start
    norm = normalize(text)
    if not text.strip():
        return {"status": "error", "detail": "empty answer", "seconds": seconds, "tools": tools}
    missing = [p for p in q.get("expect", []) if not re.search(p, norm)]
    forbidden = [p for p in q.get("forbid", []) if re.search(p, norm)]
    if not tools and re.search(r"\b429\b|rate.?limit", norm):
        status = "rate_limited"
    else:
        status = "pass" if not missing and not forbidden else "fail"
    return {"status": status, "missing": missing, "forbidden": forbidden, "seconds": seconds, "tools": tools,
            "answer": text}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default=os.environ.get("LANGFLOW_SERVER_URL", "http://localhost:7860"))
    parser.add_argument("--flow-id", default=FLOW_ID)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--tags", help="only questions with one of these tags (comma-separated)")
    parser.add_argument("--only", help="only these question ids (comma-separated)")
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--out", help="write all results as JSON to this file")
    args = parser.parse_args()

    api_key = os.environ.get("LANGFLOW_API_KEY")
    if not api_key:
        print("LANGFLOW_API_KEY is not set (run with: uv run --env-file .env python ...)", file=sys.stderr)
        return 2

    questions = json.loads(QUESTIONS.read_text(encoding="utf-8"))["questions"]
    if args.tags:
        wanted = set(args.tags.split(","))
        questions = [q for q in questions if wanted & set(q["tags"])]
    if args.only:
        wanted = set(args.only.split(","))
        questions = [q for q in questions if q["id"] in wanted]

    results = []
    for q in questions:
        for i in range(args.repeat):
            r = evaluate(q, args, api_key)
            r.update(id=q["id"], tags=q["tags"], run=i + 1)
            results.append(r)
            extra = ""
            if r["status"] == "fail":
                extra = f" missing={r['missing']} forbidden={r['forbidden']} answer={normalize(r['answer'])[:160]!r}"
            elif r["status"] in ("error", "rate_limited"):
                extra = f" {r.get('detail', '')[:160]}"
            print(f"{q['id']:<26} run {i + 1}: {r['status']:<12} {r['seconds']:5.1f}s tools={len(r.get('tools', []))}{extra}",
                  flush=True)

    def summary(label: str, rows: list[dict]) -> None:
        answered = [r for r in rows if r["status"] in ("pass", "fail")]
        passed = sum(r["status"] == "pass" for r in rows)
        tools = [len(r.get("tools", [])) for r in answered]
        secs = [r["seconds"] for r in answered]
        print(
            f"{label:<12} pass {passed}/{len(answered)} answered"
            f" ({100 * passed / len(answered):.0f}%)" if answered else f"{label:<12} no answered runs",
            end="",
        )
        print(f" | rate_limited {sum(r['status'] == 'rate_limited' for r in rows)}"
              f" | error {sum(r['status'] == 'error' for r in rows)}"
              + (f" | tools avg {statistics.mean(tools):.1f} | time avg {statistics.mean(secs):.0f}s" if answered else ""))

    print("\n== summary")
    summary("all", results)
    for tag in ("baseline", "new"):
        rows = [r for r in results if tag in r["tags"]]
        if rows:
            summary(tag, rows)
    if args.out:
        Path(args.out).write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
