"""PROTOTYPE — throwaway. One transcript-pass call on one lecture (articulate#39).

Usage: python3 run.py <recording.json> "<course>" [deck-concepts.json|-] [model]
  deck-concepts.json: [{"key": ..., "title": ...}]; "-" or absent = no deck (whole lecture off-slide).
Gemini key from the login Keychain item `articulate-gemini`. Writes to $ARTICULATE_PROTO/out
(default ~/Library/Application Support/articulate/prototypes/39-transcript-concepts/out):
<stem>.raw.json (API response) and <stem>.json (meta + windows + parsed output).
"""
import json, os, subprocess, sys, time, urllib.error, urllib.request
from pathlib import Path

from schema import SCHEMA
from windows import build, load

here = Path(__file__).parent
rec, course = Path(sys.argv[1]), sys.argv[2]
deck_arg = sys.argv[3] if len(sys.argv) > 3 else "-"
model = sys.argv[4] if len(sys.argv) > 4 else "gemini-3.6-flash"
out = Path(os.environ.get("ARTICULATE_PROTO", Path.home() / "Library/Application Support/articulate/prototypes/39-transcript-concepts")) / "out"
out.mkdir(parents=True, exist_ok=True)

key = subprocess.run(["security", "find-generic-password", "-s", "articulate-gemini", "-w"],
                     capture_output=True, text=True, check=True).stdout.strip()
data = load(rec)
wins = build(data["transcript"]["segments"])
deck = json.load(open(deck_arg)) if deck_arg != "-" else []
deck_txt = "\n".join(f"- {c['key']}: {c['title']}" for c in deck) or "(none: no slides for this lecture)"
win_txt = "\n\n".join(f"[Window {w['window']}]\n{w['text']}" for w in wins)
variant = os.environ.get("PROMPT", "prompt")  # prompt.md or e.g. PROMPT=prompt-recall
prompt = ((here / f"{variant}.md").read_text()
          .replace("{course}", course).replace("{deck}", deck_txt).replace("{windows}", win_txt))

body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "responseJsonSchema": SCHEMA}}

start, attempts = time.time(), []
for wait in (0, 15, None):  # one 503 retry, then give up (the pipeline would fall through the chain)
    if wait is None:
        sys.exit("gemini: every attempt failed: " + ", ".join(attempts))
    time.sleep(wait)
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", "x-goog-api-key": key})
    try:
        with urllib.request.urlopen(req, timeout=1200) as r:
            raw = json.load(r)
        break
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:300]
        attempts.append(str(e.code))
        if e.code not in (500, 503):
            sys.exit(f"gemini {e.code}: {detail}")

secs = round(time.time() - start, 1)
stem = f"{rec.stem[:8]}.{'deck' if deck else 'nodeck'}.{model}" + ("" if variant == "prompt" else f".{variant}")
(out / f"{stem}.raw.json").write_text(json.dumps(raw, indent=2))
cand = raw["candidates"][0]
text = "".join(p.get("text", "") for p in cand["content"]["parts"] if not p.get("thought"))
parsed = json.loads(text)
meta = {"model": model, "recording": rec.stem, "course": course, "deckConcepts": len(deck), "windows": len(wins),
        "seconds": secs, "finishReason": cand.get("finishReason"), "usage": raw.get("usageMetadata"),
        "failed_attempts": attempts}
(out / f"{stem}.json").write_text(json.dumps(
    {"meta": meta, "deck": deck, "windows": [{k: v for k, v in w.items() if k != "segments"} for w in wins],
     "output": parsed}, indent=2, ensure_ascii=False))
print(json.dumps(meta, indent=2))
print(out / f"{stem}.json")
