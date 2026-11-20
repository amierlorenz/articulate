"""PROTOTYPE — throwaway. Split a Pocket transcript into numbered 2–5 min windows (articulate#39).

Usage: python3 windows.py <recording.json> [target_sec]   → prints windows JSON
Cuts only at segment boundaries: a window closes at the first boundary past `target` seconds (default 180),
and never runs past 300 s unless a single segment does. A tail shorter than 120 s joins the previous window.
"""
import json, sys


def build(segments: list[dict], target: float = 180, cap: float = 300, min_tail: float = 120) -> list[dict]:
    wins, cur = [], []
    for seg in segments:
        if cur and (seg["end"] - cur[0]["start"] > cap or cur[-1]["end"] - cur[0]["start"] >= target):
            wins.append(cur)
            cur = []
        cur.append(seg)
    if cur:
        if wins and cur[-1]["end"] - cur[0]["start"] < min_tail:
            wins[-1] += cur
        else:
            wins.append(cur)
    return [{"window": i + 1, "startSec": w[0]["start"], "endSec": w[-1]["end"],
             "segments": w, "text": " ".join(s["text"].strip() for s in w)} for i, w in enumerate(wins)]


def load(path: str) -> dict:
    return json.load(open(path))["data"]


if __name__ == "__main__":
    data = load(sys.argv[1])
    target = float(sys.argv[2]) if len(sys.argv) > 2 else 180
    ws = build(data["transcript"]["segments"], target)
    print(json.dumps([{k: v for k, v in w.items() if k != "segments"} for w in ws], indent=2))
