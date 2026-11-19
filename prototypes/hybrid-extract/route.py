"""PROTOTYPE — throwaway. Page routing rules for hybrid Extract (articulate#26).

route(pages, rule) -> {"local": [page numbers], "gemini": [page numbers]}
`python3 route.py out/<stem>.pages.json [rule]` prints the plan and why each Gemini page went there.

Rules
- default: Gemini when the page has much image area, much text that only OCR sees (text inside images),
  unreliable OCR, or math. Everything else goes local as text layer + OCR.
- ocr: trust confident OCR of images; Gemini only for unreliable OCR or math.
"""
import json, sys

RULES = {
    "default": {"image_frac": 0.15, "ocr_only_frac": 0.15, "ocr_low_frac": 0.10, "math_density": 1.5},
    "ocr": {"image_frac": None, "ocr_only_frac": None, "ocr_low_frac": 0.10, "math_density": 1.5},
}


def reasons(p: dict, rule: str) -> list[str]:
    if rule == "all":  # every page counts as routed (hybrid-vl: transcribe every page with the VL model)
        return ["all"]
    r = []
    for sig, th in RULES[rule].items():
        if th is not None and (p[sig] or 0) >= th:
            r.append(f"{sig}={p[sig]}")
    return r


def route(pages: list[dict], rule: str = "default") -> dict:
    plan = {"local": [], "gemini": []}
    for p in pages:
        plan["gemini" if reasons(p, rule) else "local"].append(p["page"])
    return plan


if __name__ == "__main__":
    pages = json.loads(open(sys.argv[1]).read())["pages"]
    rule = sys.argv[2] if len(sys.argv) > 2 else "default"
    plan = route(pages, rule)
    print(f"{rule}: local {len(plan['local'])}, gemini {len(plan['gemini'])} of {len(pages)}")
    for p in pages:
        if (r := reasons(p, rule)):
            print(f"  p{p['page']}: {', '.join(r)}")
