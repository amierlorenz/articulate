#!/usr/bin/env python3
"""Ask Gemini for a verbatim transcript of a public YouTube video, by URL.

Stdlib only. Google fetches the video itself; this script never contacts YouTube.
The key comes from the GEMINI_API_KEY environment variable and is never printed or saved.

Examples:
    python3 run.py gemini-3.6-flash
    python3 run.py gemini-3.6-flash --low-res
    python3 run.py gemini-3.6-flash --low-res --clip 300 420
    python3 run.py gemini-3.5-flash-lite --api interactions --layers transcript

Output: out/<model>-<res>[-fpsF][-clipS-E][-interactions-<layers>].json, holding the request
body, the raw response (or error), usageMetadata, wall time and parsed segments.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request

VIDEO_URL = "https://www.youtube.com/watch?v=QLgH5_mqX28"  # 21:49, public
BASE = "https://generativelanguage.googleapis.com/v1beta"
OUT_DIR = pathlib.Path(__file__).resolve().parent / "out"

PROMPT = """Transcribe the speech in this video verbatim, word for word, in English.
Do not summarize, paraphrase, clean up, or skip anything; keep filler words.
Split the transcript into segments of one or two sentences (roughly 5-10 seconds each).
For each segment give start_sec: the time in seconds from the start of the video
when the segment's first word is spoken, and text: the exact words.
Return only JSON of the form {"segments": [{"start_sec": 0.0, "text": "..."}]}."""

SCHEMA = {
    "type": "object",
    "properties": {
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start_sec": {"type": "number"},
                    "text": {"type": "string"},
                },
                "required": ["start_sec", "text"],
            },
        }
    },
    "required": ["segments"],
}


def build_generate_content(args: argparse.Namespace) -> tuple[str, dict]:
    video_part: dict = {"fileData": {"fileUri": args.url}}
    meta: dict = {}
    if args.clip:
        meta["startOffset"] = f"{args.clip[0]}s"
        meta["endOffset"] = f"{args.clip[1]}s"
    if args.fps:
        meta["fps"] = args.fps
    if meta:
        video_part["videoMetadata"] = meta
    gen: dict = {"temperature": 0, "maxOutputTokens": args.max_output_tokens}
    if not args.no_schema:
        gen["responseMimeType"] = "application/json"
        gen["responseJsonSchema"] = SCHEMA
    if args.low_res:
        gen["mediaResolution"] = "MEDIA_RESOLUTION_LOW"
    body = {
        # Video first, then the prompt, as the docs recommend.
        "contents": [{"role": "user", "parts": [video_part, {"text": PROMPT}]}],
        "generationConfig": gen,
    }
    return f"{BASE}/models/{args.model}:generateContent", body


def build_interactions(args: argparse.Namespace) -> tuple[str, dict]:
    # Shape from https://aistudio.google.com/docs/video-understanding (Interactions API).
    video: dict = {"type": "video", "uri": args.url}
    processing: dict = {"type": "static"}
    if args.layers:
        processing["content_layers"] = args.layers.split(",")
    if args.clip:
        processing["start_offset"] = f"{args.clip[0]}s"
        processing["end_offset"] = f"{args.clip[1]}s"
    if args.fps:
        processing["fps"] = args.fps
    if len(processing) > 1:
        video["processing"] = processing
    if args.low_res:
        video["resolution"] = "low"
    body = {"model": args.model, "input": [video, {"type": "text", "text": PROMPT}]}
    return f"{BASE}/interactions", body


def out_name(args: argparse.Namespace) -> str:
    name = f"{args.model}-{'low' if args.low_res else 'default'}"
    if args.fps:
        name += f"-fps{args.fps:g}"
    if args.clip:
        name += f"-clip{args.clip[0]}-{args.clip[1]}"
    if args.no_schema:
        name += "-noschema"
    if args.api == "interactions":
        name += "-interactions"
        if args.layers:
            name += "-" + args.layers.replace(",", "+")
    return name + ".json"


def collect_text(resp: dict) -> str:
    """Join the non-thought text parts; falls back to any 'text' fields (interactions shape)."""
    parts = []
    for cand in resp.get("candidates", [])[:1]:
        for p in cand.get("content", {}).get("parts", []):
            if "text" in p and not p.get("thought"):
                parts.append(p["text"])
    if parts:
        return "".join(parts)

    found: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") in ("text", None) and isinstance(node.get("text"), str):
                found.append(node["text"])
            for k, v in node.items():
                if k not in ("input",):
                    walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(resp.get("outputs", resp))
    return "".join(found)


def parse_segments(text: str) -> list[dict] | None:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        t = t.rsplit("```", 1)[0]
    try:
        data = json.loads(t)
    except json.JSONDecodeError:
        return None
    segs = data.get("segments") if isinstance(data, dict) else data
    return segs if isinstance(segs, list) else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("model", help="model code, e.g. gemini-3.6-flash")
    ap.add_argument("--low-res", action="store_true", help="MEDIA_RESOLUTION_LOW")
    ap.add_argument("--clip", nargs=2, type=int, metavar=("START", "END"), help="seconds")
    ap.add_argument("--fps", type=float, help="frames per second sent to the model (0, 24]; default 1")
    ap.add_argument("--api", choices=["generate", "interactions"], default="generate")
    ap.add_argument("--layers", help="interactions only: content_layers, e.g. transcript or audio")
    ap.add_argument("--no-schema", action="store_true", help="drop responseJsonSchema if the model rejects it")
    ap.add_argument("--max-output-tokens", type=int, default=32768)
    ap.add_argument("--url", default=VIDEO_URL)
    ap.add_argument("--timeout", type=int, default=900)
    args = ap.parse_args()

    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        print("GEMINI_API_KEY is not set", file=sys.stderr)
        return 2

    build = build_interactions if args.api == "interactions" else build_generate_content
    endpoint, body = build(args)
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(body).encode(),
        headers={"x-goog-api-key": key, "Content-Type": "application/json"},
        method="POST",
    )

    record: dict = {"endpoint": endpoint, "request": body, "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=args.timeout) as r:
            record["http_status"] = r.status
            raw = r.read().decode()
    except urllib.error.HTTPError as e:
        record["http_status"] = e.code
        raw = e.read().decode(errors="replace")
    except (urllib.error.URLError, TimeoutError) as e:
        record["http_status"] = None
        raw = json.dumps({"client_error": repr(e)})
    record["wall_sec"] = round(time.monotonic() - t0, 2)

    try:
        resp = json.loads(raw)
    except json.JSONDecodeError:
        resp = {"unparsed_body": raw}
    record["response"] = resp
    record["usageMetadata"] = resp.get("usageMetadata") or resp.get("usage")

    text = collect_text(resp) if record["http_status"] == 200 else ""
    segs = parse_segments(text) if text else None
    record["segments"] = segs
    if text and segs is None:
        record["unparsed_text"] = text
    if resp.get("candidates"):
        record["finishReason"] = resp["candidates"][0].get("finishReason")

    OUT_DIR.mkdir(exist_ok=True)
    path = OUT_DIR / out_name(args)
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False))

    print(f"HTTP {record['http_status']}  {record['wall_sec']}s  -> {path}")
    print("usage:", json.dumps(record["usageMetadata"]))
    if record.get("finishReason"):
        print("finishReason:", record["finishReason"])
    if segs is not None:
        print(f"segments: {len(segs)}; last start_sec: {segs[-1].get('start_sec') if segs else None}")
    elif record["http_status"] != 200:
        print("error:", json.dumps(resp)[:800])
    else:
        print("could not parse segments; see unparsed_text in the output file")
    return 0 if record["http_status"] == 200 else 1


if __name__ == "__main__":
    sys.exit(main())
