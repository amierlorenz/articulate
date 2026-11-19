# /// script
# requires-python = ">=3.12"
# dependencies = ["pypdfium2"]
# ///
"""PROTOTYPE — throwaway. Gemini-only vs hybrid vs all-local Extract on one deck (articulate#26).

Usage:
  uv run extract.py gemini <deck.pdf>            whole PDF, one Gemini 3.8 Flash call (articulate#22 baseline)
  uv run extract.py hybrid <deck.pdf> [rule]     route pages (route.py), text pages -> qwen3:8b, the rest -> one Gemini call
  uv run extract.py local  <deck.pdf>            every page -> qwen3:8b (floor)
Needs out/<stem>.pages.json from prep.py. Gemini key from the login Keychain item `articulate-gemini`.
Writes out/<stem>.<leg>.json = {meta, output} (same shape check.py reads).
"""
import base64, io, json, os, re, subprocess, sys, time, unicodedata, urllib.error, urllib.request
from pathlib import Path

import pypdfium2 as pdfium

from route import route
from schema import SCHEMA

here = Path(__file__).parent
leg, deck = sys.argv[1], Path(sys.argv[2])
rule = sys.argv[3] if len(sys.argv) > 3 else "default"
out = here / "out"
prep = json.loads((out / f"{deck.stem}.pages.json").read_text())
PROMPT = (here / "prompt.md").read_text()
GEMINI_MODEL, LOCAL_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash"), os.environ.get("LOCAL_MODEL", "qwen3:8b")
CHUNK = int(os.environ.get("CHUNK", 12))  # pages per local call
calls = {"gemini": 0, "local": 0}
timing = {"gemini_s": 0.0, "local_s": 0.0}
usage = {"gemini": [], "local": []}


# ---------- Gemini ----------
def gemini_key() -> str:
    return subprocess.run(["security", "find-generic-password", "-s", "articulate-gemini", "-w"],
                          capture_output=True, text=True, check=True).stdout.strip()


def gemini(pdf_bytes: bytes, note: str = "") -> dict:
    body = {
        "contents": [{"role": "user", "parts": [
            {"inlineData": {"mimeType": "application/pdf", "data": base64.b64encode(pdf_bytes).decode()}},
            {"text": PROMPT + note},
        ]}],
        "generationConfig": {"responseMimeType": "application/json", "responseJsonSchema": SCHEMA},
    }
    key, start = gemini_key(), time.time()
    for wait in (0, 15, 45, None):
        if wait is None:
            sys.exit("gemini: every attempt failed")
        time.sleep(wait)
        req = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
            data=json.dumps(body).encode(), method="POST",
            headers={"Content-Type": "application/json", "x-goog-api-key": key})
        try:
            calls["gemini"] += 1
            with urllib.request.urlopen(req, timeout=600) as r:
                raw = json.load(r)
            break
        except urllib.error.HTTPError as e:
            print(f"gemini {e.code}: {e.read().decode()[:200]}", file=sys.stderr)
            if e.code not in (429, 500, 503):
                sys.exit(1)
        except (TimeoutError, urllib.error.URLError) as e:
            print(f"gemini timeout/network: {e}", file=sys.stderr)
    timing["gemini_s"] += time.time() - start
    cand = raw["candidates"][0]
    usage["gemini"].append({**(raw.get("usageMetadata") or {}), "finishReason": cand.get("finishReason")})
    text = "".join(p.get("text", "") for p in cand["content"]["parts"] if not p.get("thought"))
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        (out / f"{deck.stem}.gemini-fail.txt").write_text(text)
        sys.exit(f"gemini: invalid JSON, finishReason={cand.get('finishReason')}, usage={raw.get('usageMetadata')}")


def sub_pdf(pages: list[int]) -> bytes:
    src = pdfium.PdfDocument(deck)
    dst = pdfium.PdfDocument.new()
    dst.import_pages(src, [p - 1 for p in pages])
    buf = io.BytesIO()
    dst.save(buf)
    return buf.getvalue()


# ---------- local (Ollama) ----------
def words(t: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", unicodedata.normalize("NFKC", t).lower()))


VL = {}  # page -> local VL transcription (hybrid-vl leg)


def page_text(p: dict) -> str:
    """Text layer, plus OCR lines whose words are mostly missing from it (text inside images).
    In the hybrid-vl leg, a routed page uses the local VL model's transcription instead."""
    if p["page"] in VL:
        return "[slide transcribed from its image, math in LaTeX]\n" + VL[p["page"]]
    layer = words(p["text"])
    extra = [l["text"] for l in p["ocr"] if (w := words(l["text"])) and len(w - layer) / len(w) > 0.5]
    t = p["text"]
    if extra:
        t += "\n[text read from images on this slide by OCR]\n" + "\n".join(extra)
    return t


LOCAL_NOTE = """

The document is given below as text, page by page, extracted from the slides (the PDF text layer, plus OCR
of text inside images where marked). Page numbers are the deck's own: cite them exactly as given in the
=== Page N === headers. Quote text exactly as it appears below.
Keep it short: each quote is one phrase or sentence of at most 25 words, each claim text at most 2 sentences.
Never repeat a sentence. Stop when every concept on these pages is covered.
"""


def no_pattern(x):
    """Ollama 0.40.16 silently drops the whole grammar when any `pattern` is present (ollama_format_test.py)."""
    if isinstance(x, dict):
        return {k: no_pattern(v) for k, v in x.items() if k != "pattern"}
    if isinstance(x, list):
        return [no_pattern(v) for v in x]
    return x


LOCAL_SCHEMA = no_pattern(SCHEMA)


def local(pages: list[dict]) -> dict:
    doc = "\n\n".join(f"=== Page {p['page']} ===\n{page_text(p)}" for p in pages)
    body = {
        "model": LOCAL_MODEL, "stream": False, "think": False, "format": LOCAL_SCHEMA,
        "options": {"temperature": 0, "num_ctx": 16384, "num_predict": int(os.environ.get("NUM_PREDICT", 8192)), "repeat_penalty": 1.15},
        "messages": [{"role": "user", "content": PROMPT + LOCAL_NOTE + "\n" + doc}],
    }
    start = time.time()
    req = urllib.request.Request("http://localhost:11434/api/chat", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    calls["local"] += 1
    with urllib.request.urlopen(req, timeout=3600) as r:
        raw = json.load(r)
    timing["local_s"] += time.time() - start
    usage["local"].append({k: raw.get(k) for k in ("prompt_eval_count", "eval_count", "done_reason")})
    (out / f"{deck.stem}.local-raw-p{pages[0]['page']}.txt").write_text(raw["message"]["content"])
    try:
        return json.loads(raw["message"]["content"])
    except json.JSONDecodeError as e:
        print(f"local: invalid JSON ({raw.get('done_reason')}): {e}", file=sys.stderr)
        return {"concepts": [], "contrastGroups": [], "_invalid": raw["message"]["content"][-300:]}


def runs(pages: list[int]) -> list[list[int]]:
    """Contiguous runs, each cut into chunks of at most CHUNK pages."""
    out, cur = [], []
    for p in pages:
        if cur and (p != cur[-1] + 1 or len(cur) == CHUNK):
            out.append(cur)
            cur = []
        cur.append(p)
    return out + ([cur] if cur else [])


# ---------- merge ----------
def remap(part: dict, page_map: dict[int, int]) -> dict:
    """Sub-PDF page k -> deck page page_map[k], in every citation and slide hint."""
    def fix(x):
        if isinstance(x, dict):
            if "page" in x and isinstance(x["page"], int):
                x["page"] = page_map.get(x["page"], -x["page"])  # negative = out of range in the sub-PDF
            for v in x.values():
                fix(v)
        elif isinstance(x, list):
            for v in x:
                fix(v)
    fix(part)
    return part


def norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", t.lower())


def merge(parts: list[tuple[str, dict]]) -> tuple[dict, dict]:
    concepts, groups, seen, stats = [], [], {}, {"merged_duplicates": 0, "key_renames": 0}
    for tag, part in parts:
        rename = {}
        for c in part.get("concepts", []):
            c["_source"] = tag
            t = norm_title(c["title"])
            if t in seen:  # same title in two parts: keep one, append the other's claims
                stats["merged_duplicates"] += 1
                keep = seen[t]
                for f in ("useWhen", "conditions", "workedExamples", "slideHints"):
                    keep[f] += c[f]
                rename[c["key"]] = keep["key"]
                continue
            k = c["key"]
            if any(x["key"] == k for x in concepts):
                c["key"] = f"{k}-{tag}"
                rename[k] = c["key"]
                stats["key_renames"] += 1
            seen[t] = c
            concepts.append(c)
        for g in part.get("contrastGroups", []):
            g["memberKeys"] = [rename.get(k, k) for k in g["memberKeys"]]
            for ct in g["contrasts"]:
                ct["a"], ct["b"] = rename.get(ct["a"], ct["a"]), rename.get(ct["b"], ct["b"])
            g["_source"] = tag
            groups.append(g)
    return {"concepts": concepts, "contrastGroups": groups}, stats


# ---------- legs ----------
t0 = time.time()
all_pages = [p for p in prep["pages"] if not os.environ.get("PAGES") or str(p["page"]) in os.environ["PAGES"].split(",")]
plan = {"local": [], "gemini": []}
merge_stats = {}
if leg == "gemini":
    output = gemini(deck.read_bytes())
    plan["gemini"] = [p["page"] for p in all_pages]
elif leg in ("hybrid", "local", "hybrid-vl"):
    if leg == "local":
        plan["local"] = [p["page"] for p in all_pages]
    elif leg == "hybrid-vl":  # routed pages -> local VL transcription (vl_transcribe.py), all pages -> qwen3:8b
        vl = json.loads((out / f"{deck.stem}.vl.json").read_text())
        routed = route(all_pages, rule)["gemini"]
        missing = [p for p in routed if str(p) not in vl]
        if missing:
            sys.exit(f"run vl_transcribe.py first; missing pages {missing}")
        VL.update({p: vl[str(p)]["text"] for p in routed})
        timing["local_s"] += sum(vl[str(p)]["seconds"] for p in routed)
        plan = {"local": [p["page"] for p in all_pages], "gemini": [], "vl": routed}
    else:
        plan = route(all_pages, rule)
    parts = []
    by_page = {p["page"]: p for p in all_pages}
    for chunk in runs(plan["local"]):
        print(f"local pages {chunk[0]}-{chunk[-1]}", file=sys.stderr)
        parts.append((f"local{chunk[0]}", local([by_page[p] for p in chunk])))
    if plan["gemini"]:
        print(f"gemini pages {plan['gemini']}", file=sys.stderr)
        g = gemini(sub_pdf(plan["gemini"]),
                   "\n\nThis PDF holds only some slides of a longer deck; cite page numbers within this PDF (1-based).")
        parts.append(("gemini", remap(g, {i + 1: p for i, p in enumerate(plan["gemini"])})))
    output, merge_stats = merge(parts)
else:
    sys.exit(f"unknown leg {leg}")

meta = {"leg": leg, "rule": rule if leg.startswith("hybrid") else None, "deck": deck.name,
        "model": {"gemini": GEMINI_MODEL, "local": LOCAL_MODEL, "hybrid": f"{LOCAL_MODEL}+{GEMINI_MODEL}",
                  "hybrid-vl": f"{LOCAL_MODEL} on qwen3-vl:8b-instruct transcripts"}[leg],
        "seconds": round(time.time() - t0, 1), "gemini_seconds": round(timing["gemini_s"], 1),
        "local_seconds": round(timing["local_s"], 1), "calls": calls, "plan": plan,
        "merge": merge_stats, "usage": usage, "finishReason": None}
name = f"{deck.stem}.{leg}" + (f"-{rule}" if leg.startswith("hybrid") and rule != "default" else "")
(out / f"{name}.json").write_text(json.dumps({"meta": meta, "output": output}, indent=2, ensure_ascii=False))
print(json.dumps({k: meta[k] for k in ("leg", "seconds", "gemini_seconds", "local_seconds", "calls", "plan", "merge")}))
