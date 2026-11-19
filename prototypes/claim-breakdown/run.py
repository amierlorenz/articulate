"""PROTOTYPE (articulate#30): claim breakdown from a guest speaker. Throwaway, stdlib only.

    python3 prototypes/claim-breakdown/run.py            # both variants
    python3 prototypes/claim-breakdown/run.py atomic     # one variant

Reads the Pocket key from the Keychain (service `articulate`, account `pocket_api_key`), caches raw recordings in
raw/ (git-ignored), writes notes to out/<variant>/ and a quote check to out/<variant>/CHECK.md (PROMPT.txt is
git-ignored: it lists personal vault titles). `--check [variant]` re-checks saved output without the model.
Nothing is written to the vault.
"""
import json, os, re, subprocess, sys, urllib.request
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

HERE = Path(__file__).parent
RAW, OUT = HERE / "raw", HERE / "out"
VAULT = Path.home() / "Library/Mobile Documents/iCloud~md~obsidian/Documents/iCloud Vault"
API = "https://public.heypocketai.com/api/v1/public/recordings"

COMMAND_ID = "5b20a9f2-462b-4825-b8e1-2b5980fb1cae"
SOURCE_ID = "9891829e-b86b-4574-89b1-6c7ed21912cb"
# What the command resolver would hand over (decided in "Voice commands for acting on recordings", #29).
SPAN = (6060, 6630)            # seconds; talk runs inside one 1:38:55–1:50:30 Pocket segment
SPAN_START_PHRASE = "we don't have as much time on this"
SPAN_END_PHRASE = "happy to kind of chat offline on this as well"
SPEAKER = "Ayush Parikh"
SOURCE_HUB = "AI301 Live Class"  # would come from the calendar match + courses.toml; no hub exists yet
MODEL = "sonnet"
MARKER_EVERY = 30              # seconds between ≈timestamp markers


def recording(rid):
    RAW.mkdir(exist_ok=True)
    f = RAW / f"{rid}.json"
    if not f.exists():
        key = subprocess.run(["security", "find-generic-password", "-s", "articulate", "-a", "pocket_api_key", "-w"],
                             capture_output=True, text=True, check=True).stdout.strip()
        req = urllib.request.Request(f"{API}/{rid}?include_transcript=true", headers={"Authorization": f"Bearer {key}"})
        f.write_bytes(urllib.request.urlopen(req).read())
    return json.loads(f.read_text())["data"]


def hms(t):
    t = int(t); return f"{t // 3600}:{t // 60 % 60:02}:{t % 60:02}"


def span_text(rec):
    """Span text with ≈markers. Pocket gives one start/end per segment, so times inside a segment are
    interpolated by character position (speech rate assumed constant)."""
    out, last_marker = [], -1e9
    for s in rec["transcript"]["segments"]:
        if s["end"] < SPAN[0] or s["start"] > SPAN[1]:
            continue
        text, dur = s["text"], s["end"] - s["start"]
        lo = text.find(SPAN_START_PHRASE) if SPAN_START_PHRASE in text else 0
        hi = text.find(SPAN_END_PHRASE) + len(SPAN_END_PHRASE) if SPAN_END_PHRASE in text else len(text)
        for m in re.finditer(r"[^.?!]+[.?!]*\s*", text[lo:hi]):
            t = s["start"] + dur * (lo + m.start()) / max(len(text), 1)
            if t - last_marker >= MARKER_EVERY:
                out.append(f"\n[≈{hms(t)}] "); last_marker = t
            out.append(m.group(0))
    return "".join(out).strip()


def vault_titles():
    titles = []
    for d in ["Literature Notes", "Permanent Notes", "Concepts"]:
        titles += [f"{d}/{p.stem}" for p in sorted((VAULT / d).glob("*.md"))]
    return titles


def norm(s):
    s = s.lower().replace("’", "'")
    return " ".join(re.sub(r"[^a-z0-9' ]+", " ", s).split())


def check_quote(q, transcript_norm):
    """Every piece outside […] and … must appear in the transcript, in order."""
    pieces = [norm(p) for p in re.split(r"\[[^\]]*\]|…|\.\.\.", q)]
    pos = 0
    for p in filter(None, pieces):
        i = transcript_norm.find(p, pos)
        if i < 0:
            return False, p
        pos = i + len(p)
    return True, ""


def run(variant, src, cmd):
    rec_at = datetime.fromisoformat(src["recording_at"].replace("Z", "+00:00")).astimezone(timezone(timedelta(hours=-5)))
    prompt = f"""Variant: {variant}
Today: {date.today().isoformat()}

Spoken command (recording {COMMAND_ID}, "{cmd['title']}"), verbatim transcript:
<command>{cmd['transcript']['text']}</command>

Speaker: {SPEAKER}, the instructor of the CodePath AI 301 class (name and role from the command; Pocket's speaker
labels are wrong). Only describe credentials the transcript or command actually states.
Source recording: "{src['title'].strip()}", id {SOURCE_ID}, recorded {rec_at.isoformat(timespec='minutes')}.
Source hub to link: [[{SOURCE_HUB}]]. Speaker note to link: [[{SPEAKER}]].

Existing vault notes (folder/title) you may link to:
<vault>
{chr(10).join(vault_titles())}
</vault>

Transcript span ({hms(SPAN[0])}–{hms(SPAN[1])}, ≈markers are estimates):
<transcript>
{span_text(src)}
</transcript>
"""
    d = OUT / variant
    d.mkdir(parents=True, exist_ok=True)
    (d / "PROMPT.txt").write_text(prompt)
    t0 = datetime.now()
    res = subprocess.run(["claude", "-p", "--model", MODEL, "--tools", "", "--output-format", "json",
                          "--system-prompt-file", str(HERE / "RECIPE.md")],
                         input=prompt, capture_output=True, text=True, check=True)
    meta = json.loads(res.stdout)
    body = meta["result"]
    (d / "RAW_OUTPUT.md").write_text(body)

    (d / "META.json").write_text(json.dumps({k: v for k, v in meta.items() if k != "result"}, indent=1))
    write_and_check(d, body, src, f"model {MODEL}, {(datetime.now() - t0).seconds}s, "
                    f"cost ${meta.get('total_cost_usd', 0):.3f} (Pro plan: counts against usage, not billed)")
    print(f"{variant} → {d}")


def write_and_check(d, body, src, header):
    main, _, notes = body.partition("=== NOTES ===")
    files = re.split(r"^=== FILE: (.+?) ===\s*$", main, flags=re.M)[1:]
    full = norm(" ".join(s["text"] for s in src["transcript"]["segments"]))
    report = [f"# {d.name}: quote check\n", header + "\n"]
    for name, content in zip(files[::2], files[1::2]):
        (d / name.strip()).write_text(content.strip() + "\n")
        report.append(f"\n## {name.strip()}\n")
        # citation quotes only: "…" — Speaker
        for q in re.findall(r"[\"“]([^\"”\n]{10,})[\"”]\s*[—-]", content):
            ok, miss = check_quote(q, full)
            report.append(f"- {'OK  ' if ok else 'FAIL'} \"{q[:90]}{'…' if len(q) > 90 else ''}\""
                          + ("" if ok else f"\n  - not found: `{miss}`"))
    report.append("\n## Model's judgement notes\n" + notes.strip())
    (d / "CHECK.md").write_text("\n".join(report) + "\n")


if __name__ == "__main__":
    src, cmd = recording(SOURCE_ID), recording(COMMAND_ID)
    args = sys.argv[1:]
    if args[:1] == ["--check"]:   # re-check saved output without calling the model
        for v in args[1:] or ["atomic", "talk"]:
            d = OUT / v
            write_and_check(d, (d / "RAW_OUTPUT.md").read_text(), src, (d / "CHECK.md").read_text().split("\n")[2])
    else:
        for v in args or ["atomic", "talk"]:
            run(v, src, cmd)
