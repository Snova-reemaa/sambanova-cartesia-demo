"""Turn the recorded requests into single flattened prompts, plus an index.

    venv/bin/python bench/export.py

Reads bench/raw/requests.jsonl (what the recorder wrote) and
bench/raw/turns.jsonl (when run.py played each turn), and writes:

    bench/prompts/NNN-<scenario>-t<turn>-<kind>.txt   one request each
    bench/prompts/index.csv, index.json                what each file is
    bench/prompts/core/                                the main comparison set:
                                                       answer, answer-after-tools
    bench/prompts/fact-check/                          the fact-checks, as an extra set
    bench/prompts/core.jsonl, fact-check.jsonl, all.jsonl
                                                       the same three sets in the
                                                       benchmarking kit's format

Each prompt file is the request's messages and tools, exactly as sent, as
one line of JSON: {"messages": [...], "tools": [...]}. No roles are
stripped and nothing is summarised; it is flattened only in the sense
that it is one string rather than a chat request.
"""

import bisect
import csv
import json
import os
import re
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "raw")
OUT = os.path.join(HERE, "prompts")

# What each VoxNova function's call is for. The caller is read off the
# stack by the recorder; the line number is dropped here.
KIND = {
    "_run": "answer",                     # the turn's first model round, tools offered
    "generate_answer": "answer-after-tools",   # the same turn, given the tool results
    "generate": "answer-retry",           # said nothing after tools, or a held sentence rewritten
    "check_sentence": "fact-check",       # one sentence checked against the sources
    "write_up": "write-up",               # the call summarised after hang-up
    "collect_text": "thinker",            # the reasoning model on a hard question
    "in_english": "translate",
    "describe_images": "vision",
    "extract": "slot-extract",
}


def approx_tokens(text):
    """About four characters a token: an estimate, labelled as one."""
    return round(len(text) / 4)


def check_jsonl(path, rows):
    """Every line parses, has the "prompt" key and no other, holds a string
    identical to its row's prompt file, and that string is our flattened
    request; and there is one line per row, in the same order."""
    problems = []
    lines = open(path, encoding="utf-8").read().split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if len(lines) != len(rows):
        problems.append(f"{len(lines)} lines for {len(rows)} rows")
    for n, (line, row) in enumerate(zip(lines, rows), 1):
        try:
            obj = json.loads(line)
        except ValueError as exc:
            problems.append(f"line {n}: not JSON ({exc})")
            continue
        if not isinstance(obj, dict) or set(obj) != {"prompt"}:
            problems.append(f"line {n}: keys {sorted(obj) if isinstance(obj, dict) else type(obj).__name__}")
            continue
        if not isinstance(obj["prompt"], str):
            problems.append(f"line {n}: prompt is {type(obj['prompt']).__name__}, not a string")
            continue
        if obj["prompt"] != open(os.path.join(OUT, row["file"]), encoding="utf-8").read():
            problems.append(f"line {n}: does not match {row['file']}")
        inner = json.loads(obj["prompt"])
        if set(inner) != {"messages", "tools"}:
            problems.append(f"line {n}: the prompt is not a flattened request")
    return problems


def main():
    requests = [json.loads(l) for l in open(os.path.join(RAW, "requests.jsonl"), encoding="utf-8")]
    turns = [json.loads(l) for l in open(os.path.join(RAW, "turns.jsonl"), encoding="utf-8")]
    turns.sort(key=lambda t: t["started"])
    starts = [t["started"] for t in turns]
    requests.sort(key=lambda r: r["started"])

    os.makedirs(OUT, exist_ok=True)
    for old in os.listdir(OUT):
        if old.endswith(".txt") or old.startswith("index."):
            os.remove(os.path.join(OUT, old))

    rows, unplaced = [], 0
    for n, r in enumerate(requests, 1):
        at = bisect.bisect_right(starts, r["started"]) - 1
        if at < 0:
            unplaced += 1
            continue
        turn = turns[at]
        function = r["caller"].split(":")[1] if ":" in r["caller"] else r["caller"]
        kind = KIND.get(function, function)
        req = r["request"]
        prompt = json.dumps({"messages": req.get("messages", []), "tools": req.get("tools") or []},
                            ensure_ascii=False, separators=(",", ":"))
        tag = f"t{turn['turn']}" if isinstance(turn["turn"], int) else "hangup"
        name = f"{n:03d}-{turn['scenario']}-{tag}-{kind}.txt"
        with open(os.path.join(OUT, name), "w", encoding="utf-8") as f:
            f.write(prompt)
        usage = (r.get("response") or {}).get("usage") or {}
        rows.append({
            "file": name, "scenario": turn["scenario"], "turn": turn["turn"],
            "kind": kind, "caller": r["caller"], "model": req.get("model"),
            "stream": bool(req.get("stream")), "messages": len(req.get("messages", [])),
            "tools": len(req.get("tools") or []), "chars": len(prompt),
            "approx_input_tokens": approx_tokens(prompt),
            "sambanova_prompt_tokens": usage.get("prompt_tokens"),
            "response": (r.get("response") or {}).get("how"),
        })

    with open(os.path.join(OUT, "index.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    with open(os.path.join(OUT, "index.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    # Every turn's answer carries the whole conversation so far: each earlier
    # thing the caller said is in it, as a user message.
    said = {}
    for t in turns:
        if isinstance(t["turn"], int):
            said.setdefault(t["scenario"], {})[t["turn"]] = t["said"]
    gaps = []
    for r, row in zip([r for r in requests], rows):
        if row["kind"] != "answer" or not isinstance(row["turn"], int):
            continue
        users = [m.get("content") for m in r["request"].get("messages", [])
                 if m.get("role") == "user" and isinstance(m.get("content"), str)]
        for earlier in range(1, row["turn"]):
            text = said[row["scenario"]][earlier]
            if not any(text in u for u in users):
                gaps.append(f"{row['file']}: turn {earlier} ({text[:40]!r}) not in history")

    # The sets that go to the comparison. "core" is what a caller waits on:
    # the answer, and the answer again once a tool has run. The fact-checks
    # are many, small and alike, and go as an extra set of their own.
    subsets = {"core": ("answer", "answer-after-tools"), "fact-check": ("fact-check",)}
    for folder, kinds in subsets.items():
        where = os.path.join(OUT, folder)
        os.makedirs(where, exist_ok=True)
        for old in os.listdir(where):
            os.remove(os.path.join(where, old))
        picked = [row for row in rows if row["kind"] in kinds]
        for row in picked:
            shutil.copyfile(os.path.join(OUT, row["file"]), os.path.join(where, row["file"]))
        with open(os.path.join(where, "index.json"), "w", encoding="utf-8") as f:
            json.dump(picked, f, ensure_ascii=False, indent=1)
        with open(os.path.join(where, "index.csv"), "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(picked)
        sizes = [row["approx_input_tokens"] for row in picked]
        print(f"  {folder}/: {len(picked)} prompts, approx input tokens "
              f"{min(sizes)}–{max(sizes)}")

    # The benchmarking kit's input (ai-starter-kit benchmarking/prompts/
    # custom_prompt_example.jsonl): one {"prompt": "<text>"} per line, and
    # nothing else. The text is the flattened prompt as above, a string. Line
    # n is row n of the matching index, so results map back to it.
    sets = {"core": [row for row in rows if row["kind"] in subsets["core"]],
            "fact-check": [row for row in rows if row["kind"] in subsets["fact-check"]],
            "all": rows}
    for name, picked in sets.items():
        path = os.path.join(OUT, f"{name}.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for row in picked:
                text = open(os.path.join(OUT, row["file"]), encoding="utf-8").read()
                f.write(json.dumps({"prompt": text}, ensure_ascii=False) + "\n")
        problems = check_jsonl(path, picked)
        print(f"  {name}.jsonl: {len(picked)} lines, "
              + ("every line valid" if not problems else f"{len(problems)} PROBLEMS"))
        for problem in problems[:5]:
            print("    " + problem)
        if problems:
            raise SystemExit(1)

    by_kind = {}
    for row in rows:
        by_kind[row["kind"]] = by_kind.get(row["kind"], 0) + 1
    tokens = [row["approx_input_tokens"] for row in rows]
    real = [row["sambanova_prompt_tokens"] for row in rows if row["sambanova_prompt_tokens"]]
    print(f"  {len(rows)} prompts in {os.path.relpath(OUT)}; {unplaced} outside any turn")
    print("  by kind: " + ", ".join(f"{k} {v}" for k, v in sorted(by_kind.items(), key=lambda kv: -kv[1])))
    print(f"  approx input tokens: {min(tokens)}–{max(tokens)}, median {sorted(tokens)[len(tokens)//2]}")
    if real:
        print(f"  SambaNova prompt_tokens: {min(real)}–{max(real)}")
    print(f"  history check: {'every answer carries every earlier turn' if not gaps else len(gaps)} ")
    for g in gaps[:10]:
        print("    " + g)


if __name__ == "__main__":
    main()
