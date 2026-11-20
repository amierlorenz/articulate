#!/usr/bin/env python3
"""Score a Gemini transcript (out/*.json from run.py) against reference YouTube captions (json3).

Reports word error rate (WER) and timestamp drift. Stdlib only.

Drift: for every hypothesis segment, its first word is aligned to the reference words
(difflib on normalised words); drift = hyp start_sec - reference time of that word, where a
reference word's time is interpolated across its caption event (tStartMs + dDurationMs).

    python3 compare.py out/gemini-3.6-flash-default.json
    python3 compare.py out/gemini-3.6-flash-low-clip300-420.json --clip 300 420
    python3 compare.py --self-test          # reference against itself: WER 0, drift 0

With --clip, the reference is cut to [START, END) and drift is reported both for hyp times
taken as absolute and as relative to START (the docs don't say which the model returns).
"""

from __future__ import annotations

import argparse
import difflib
import json
import pathlib
import re
import statistics
import sys

REF = pathlib.Path("/Users/amierld/.claude/jobs/2abab32b/tmp/ref/QLgH5_mqX28.en.json3")

_TAG = re.compile(r"\[[^\]]*\]")  # [music], [applause]
_NONWORD = re.compile(r"[^a-z0-9' ]+")


def norm_words(text: str) -> list[str]:
    t = _TAG.sub(" ", text.lower().replace(" ", " "))
    t = t.replace("’", "'").replace("-", " ")
    t = _NONWORD.sub(" ", t)
    return [w.strip("'") for w in t.split() if w.strip("'")]


def load_ref(path: pathlib.Path) -> list[tuple[str, float]]:
    """(word, time_sec) for every reference word."""
    data = json.loads(path.read_text())
    out: list[tuple[str, float]] = []
    for ev in data.get("events", []):
        segs = ev.get("segs")
        if not segs:
            continue
        words = norm_words("".join(s.get("utf8", "") for s in segs))
        if not words:
            continue
        start = ev.get("tStartMs", 0) / 1000
        dur = ev.get("dDurationMs", 0) / 1000
        for i, w in enumerate(words):
            out.append((w, start + dur * i / len(words)))
    return out


def ref_as_segments(path: pathlib.Path) -> list[dict]:
    data = json.loads(path.read_text())
    return [
        {"start_sec": ev.get("tStartMs", 0) / 1000, "text": "".join(s.get("utf8", "") for s in ev["segs"])}
        for ev in data.get("events", [])
        if ev.get("segs")
    ]


def load_hyp(path: pathlib.Path) -> list[dict]:
    data = json.loads(path.read_text())
    if "events" in data:  # a json3 file
        return ref_as_segments(path)
    segs = data.get("segments") if isinstance(data, dict) else data
    if not segs:
        sys.exit(f"{path}: no parsed segments (status {data.get('http_status')})")
    return segs


def wer(ref: list[str], hyp: list[str]) -> tuple[float, int, int, int]:
    """Word-level Levenshtein: returns (wer, substitutions, deletions, insertions)."""
    n, m = len(ref), len(hyp)
    prev = [(j, 0, 0, j) for j in range(m + 1)]  # (cost, sub, del, ins)
    for i in range(1, n + 1):
        cur = [(i, 0, i, 0)] + [None] * m
        for j in range(1, m + 1):
            if ref[i - 1] == hyp[j - 1]:
                cur[j] = prev[j - 1]
                continue
            s, d, a = prev[j - 1], prev[j], cur[j - 1]
            best = min(s, d, a, key=lambda x: x[0])
            if best is s:
                cur[j] = (s[0] + 1, s[1] + 1, s[2], s[3])
            elif best is d:
                cur[j] = (d[0] + 1, d[1], d[2] + 1, d[3])
            else:
                cur[j] = (a[0] + 1, a[1], a[2], a[3] + 1)
        prev = cur
    cost, sub, dele, ins = prev[m]
    return (cost / n if n else 0.0), sub, dele, ins


def drift(ref: list[tuple[str, float]], segs: list[dict], offset: float) -> dict:
    ref_words = [w for w, _ in ref]
    hyp_words: list[str] = []
    seg_first: list[tuple[int, float]] = []  # (index into hyp_words, start_sec)
    for s in segs:
        ws = norm_words(str(s.get("text", "")))
        if ws:
            seg_first.append((len(hyp_words), float(s.get("start_sec", 0)) + offset))
            hyp_words.extend(ws)
    sm = difflib.SequenceMatcher(None, ref_words, hyp_words, autojunk=False)
    h2r: dict[int, int] = {}
    for a, b, size in sm.get_matching_blocks():
        for k in range(size):
            h2r[b + k] = a + k
    d = [t - ref[h2r[hi]][1] for hi, t in seg_first if hi in h2r]
    if not d:
        return {"aligned_segments": 0, "total_segments": len(seg_first)}
    absd = sorted(abs(x) for x in d)
    return {
        "aligned_segments": len(d),
        "total_segments": len(seg_first),
        "median_drift_sec": round(statistics.median(d), 2),
        "mean_abs_drift_sec": round(statistics.mean(absd), 2),
        "p90_abs_drift_sec": round(absd[int(0.9 * (len(absd) - 1))], 2),
        "max_abs_drift_sec": round(absd[-1], 2),
        "within_2s": round(sum(x <= 2 for x in absd) / len(absd), 3),
    }


def score(hyp_path: pathlib.Path, ref_path: pathlib.Path, clip: tuple[int, int] | None) -> dict:
    ref = load_ref(ref_path)
    segs = load_hyp(hyp_path)
    if clip:
        ref = [(w, t) for w, t in ref if clip[0] <= t < clip[1]]
    hyp_words = [w for s in segs for w in norm_words(str(s.get("text", "")))]
    rate, sub, dele, ins = wer([w for w, _ in ref], hyp_words)
    result = {
        "hyp": str(hyp_path),
        "ref_words": len(ref),
        "hyp_words": len(hyp_words),
        "wer": round(rate, 4),
        "substitutions": sub,
        "deletions": dele,
        "insertions": ins,
        "last_hyp_start_sec": segs[-1].get("start_sec") if segs else None,
    }
    if clip:
        result["drift_absolute"] = drift(ref, segs, 0)
        result["drift_relative_to_clip"] = drift(ref, segs, clip[0])
    else:
        result["drift"] = drift(ref, segs, 0)
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("hyp", nargs="*", type=pathlib.Path, help="out/*.json from run.py (or a json3 file)")
    ap.add_argument("--ref", type=pathlib.Path, default=REF)
    ap.add_argument("--clip", nargs=2, type=int, metavar=("START", "END"))
    ap.add_argument("--self-test", action="store_true", help="score the reference against itself")
    args = ap.parse_args()

    clip = tuple(args.clip) if args.clip else None
    hyps = [args.ref] if args.self_test else args.hyp
    if not hyps:
        ap.error("give at least one hypothesis file, or --self-test")
    for h in hyps:
        print(json.dumps(score(h, args.ref, clip), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
