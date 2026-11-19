# /// script
# requires-python = ">=3.12"
# dependencies = ["pypdfium2", "pillow"]
# ///
"""PROTOTYPE — throwaway. Local VL transcription of slides (from articulate#26/#35; reused as ground truth for #40).

Usage: uv run vl_transcribe.py <deck.pdf> [rule|all|p1,p2,...] [model]
Renders the chosen pages at 2x and asks the VL model for a verbatim transcription with math as LaTeX.
Default pages: the ones route.py's rule sends to Gemini. Writes/updates out/<stem>.vl.json {page: text}.
"""
import base64, io, json, sys, time, urllib.request
from pathlib import Path

import pypdfium2 as pdfium

here = Path(__file__).parent
DATA = Path.home() / "Library/Application Support/articulate/prototypes/40-damaged-layer"
deck = Path(sys.argv[1])
sel = sys.argv[2] if len(sys.argv) > 2 else "default"
model = sys.argv[3] if len(sys.argv) > 3 else "qwen3-vl:8b"
out = DATA / "out"
out.mkdir(parents=True, exist_ok=True)
if sel == "all":
    pages = list(range(1, len(pdfium.PdfDocument(deck)) + 1))
elif sel[0].isdigit():
    pages = [int(x) for x in sel.split(",")]
else:
    sys.exit("pages: all or a comma list")

PROMPT = ("Transcribe everything written on this slide, in reading order, exactly as written. "
          "Include text inside images, tables and diagrams. Write every formula and math symbol as LaTeX in $...$. "
          "Do not explain, summarise or add anything that is not on the slide.")

dest = out / f"{deck.stem}.vl.json"
res = json.loads(dest.read_text()) if dest.exists() else {}
pages = [n for n in pages if str(n) not in res]
pdf = pdfium.PdfDocument(deck)
for n in pages:
    buf = io.BytesIO()
    pdf[n - 1].render(scale=2).to_pil().save(buf, format="PNG")
    body = {"model": model, "stream": False, "think": False,
            "options": {"temperature": 0, "num_predict": 6144, "num_ctx": 12288, "repeat_penalty": 1.1},
            "messages": [{"role": "user", "content": PROMPT, "images": [base64.b64encode(buf.getvalue()).decode()]}]}
    t0 = time.time()
    req = urllib.request.Request("http://localhost:11434/api/chat", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=1200) as r:
        raw = json.load(r)
    secs = round(time.time() - t0, 1)
    res[str(n)] = {"text": raw["message"]["content"], "thinking_chars": len(raw["message"].get("thinking") or ""),
                   "seconds": secs, "model": model,
                   "done_reason": raw.get("done_reason"), "eval_count": raw.get("eval_count")}
    dest.write_text(json.dumps(res, indent=1, ensure_ascii=False))
    print(f"p{n}: {secs} s, {raw.get('eval_count')} tokens, {raw.get('done_reason')}, "
          f"text {len(raw['message']['content'])} chars, thinking {res[str(n)]['thinking_chars']} chars", flush=True)
