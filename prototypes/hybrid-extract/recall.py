"""PROTOTYPE — throwaway. Concept recall against a hand list (articulate#36).

Usage: python3 recall.py out/<stem>.<leg>.json [--quiet]
Reads gold/<stem>.json. A gold item is hit when any concept's title, key or aliases match its regex.
Concepts that hit no gold item are printed as `extra` for a human to call noise or a missed gold item.
"""
import json, re, sys
from pathlib import Path

path = Path(sys.argv[1])
quiet = "--quiet" in sys.argv
out = json.loads(path.read_text())["output"]
stem = path.name.split(".")[0]
gold = json.loads((Path(__file__).parent / "gold" / f"{stem}.json").read_text())["items"]


def text(c):
    return [c["title"], c["key"].replace("-", " "), *c.get("aliases", [])]


hits = {g["id"]: [] for g in gold}
extra = []
for c in out["concepts"]:
    matched = [g["id"] for g in gold if any(re.search(g["match"], t, re.I) for t in text(c))]
    for gid in matched:
        hits[gid].append(c["title"])
    if not matched:
        extra.append(c["title"])

tiers = {t: [g for g in gold if g["tier"] == t] for t in ("core", "minor")}
row = {"file": path.name, "concepts": len(out["concepts"]), "groups": len(out["contrastGroups"])}
for t, items in tiers.items():
    row[f"{t}_hit"] = sum(1 for g in items if hits[g["id"]])
    row[f"{t}_total"] = len(items)
row["recall_core"] = round(row["core_hit"] / row["core_total"], 2)
row["recall_all"] = round((row["core_hit"] + row["minor_hit"]) / len(gold), 2)
row["extra"] = len(extra)
row["multi_hit"] = sum(1 for v in hits.values() if len(v) > 1)
if not quiet:
    for g in gold:
        mark = "✓" if hits[g["id"]] else "·"
        print(f"  {mark} {g['tier']:5} p{g['pages'][0]:<3} {g['id']:32} {' | '.join(hits[g['id']])}")
    for e in extra:
        print(f"  + extra: {e}")
print(json.dumps(row))
