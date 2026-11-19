"""PROTOTYPE — throwaway. One Extract call on one deck (articulate#22).

Usage: GEMINI_API_KEY=… python3 run.py <deck.pdf> [model]
Writes out/<deck-stem>.raw.json (full API response) and out/<deck-stem>.json (the parsed output + meta).
Retries 503/429/500 with 15 s then 45 s backoff on the same model (no chain: the ticket validates 3.8 Flash).
"""
import base64, json, os, sys, time, urllib.error, urllib.request
from pathlib import Path

from schema import SCHEMA

here = Path(__file__).parent
deck = Path(sys.argv[1])
model = sys.argv[2] if len(sys.argv) > 2 else "gemini-3.8-flash"
key = os.environ["GEMINI_API_KEY"]
out = here / "out"
out.mkdir(exist_ok=True)

body = {
    "contents": [{"role": "user", "parts": [
        {"inlineData": {"mimeType": "application/pdf", "data": base64.b64encode(deck.read_bytes()).decode()}},
        {"text": (here / "prompt.md").read_text()},
    ]}],
    "generationConfig": {"responseMimeType": "application/json", "responseJsonSchema": SCHEMA},
}

start, attempts = time.time(), []
for wait in (0, 15, 45, None):
    if wait is None:
        sys.exit("gemini: every attempt failed: " + ", ".join(attempts))
    time.sleep(wait)
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", "x-goog-api-key": key})
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            raw = json.load(r)
        break
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:300]
        attempts.append(f"{e.code}")
        if e.code not in (429, 500, 503):
            sys.exit(f"gemini {e.code}: {detail}")
        print(f"{e.code}, retrying", file=sys.stderr)

secs = round(time.time() - start, 1)
(out / f"{deck.stem}.raw.json").write_text(json.dumps(raw, indent=2))
cand = raw["candidates"][0]
finish = cand.get("finishReason")
text = "".join(p.get("text", "") for p in cand["content"]["parts"] if not p.get("thought"))
try:
    parsed = json.loads(text)
except json.JSONDecodeError as e:
    sys.exit(f"invalid JSON (finishReason={finish}): {e}")
meta = {"model": model, "deck": deck.name, "seconds": secs, "finishReason": finish,
        "usage": raw.get("usageMetadata"), "failed_attempts": attempts}
(out / f"{deck.stem}.json").write_text(json.dumps({"meta": meta, "output": parsed}, indent=2, ensure_ascii=False))
print(json.dumps(meta, indent=2))
