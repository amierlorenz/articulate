"""PROTOTYPE — throwaway. One guide/exam call on one document (articulate#32).

Usage: GEMINI_API_KEY=… python3 run_exam.py <doc.pdf> <course.json> [model]
course.json: {"exams": [...names], "units": [...names]} (stands in for courses.toml + Canvas modules).
Writes out/<stem>.raw.json and out/<stem>.json, like run.py.
"""
import base64, json, os, sys, time, urllib.error, urllib.request
from pathlib import Path

from schema_exam import SCHEMA

here = Path(__file__).parent
doc, course = Path(sys.argv[1]), json.loads(Path(sys.argv[2]).read_text())
model = sys.argv[3] if len(sys.argv) > 3 else "gemini-3.8-flash"
key = os.environ["GEMINI_API_KEY"]
out = Path(os.environ.get("OUT_DIR", here / "out"))
tag = "." + model.removeprefix("gemini-")  # articulate#37: one output per model
out.mkdir(exist_ok=True)

lists = ("EXAM LIST:\n" + "\n".join(f"- {e}" for e in course["exams"]) +
         "\n\nUNIT LIST:\n" + "\n".join(f"- {u}" for u in course["units"]))
body = {
    "contents": [{"role": "user", "parts": [
        {"inlineData": {"mimeType": "application/pdf", "data": base64.b64encode(doc.read_bytes()).decode()}},
        {"text": (here / "prompt_exam.md").read_text() + "\n" + lists},
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
        with urllib.request.urlopen(req, timeout=1200) as r:
            raw = json.load(r)
        break
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:300]
        attempts.append(f"{e.code}")
        if e.code not in (429, 500, 503):
            sys.exit(f"gemini {e.code}: {detail}")
        print(f"{e.code}, retrying", file=sys.stderr)

secs = round(time.time() - start, 1)
(out / f"{doc.stem}{tag}.raw.json").write_text(json.dumps(raw, indent=2))
cand = raw["candidates"][0]
finish = cand.get("finishReason")
text = "".join(p.get("text", "") for p in cand["content"]["parts"] if not p.get("thought"))
try:
    parsed = json.loads(text)
except json.JSONDecodeError as e:
    sys.exit(f"invalid JSON (finishReason={finish}): {e}")
meta = {"model": model, "doc": doc.name, "seconds": secs, "finishReason": finish,
        "usage": raw.get("usageMetadata"), "failed_attempts": attempts, "course": course}
(out / f"{doc.stem}{tag}.json").write_text(json.dumps({"meta": meta, "output": parsed}, indent=2, ensure_ascii=False))
print(json.dumps({k: v for k, v in meta.items() if k != "course"}, indent=2))
