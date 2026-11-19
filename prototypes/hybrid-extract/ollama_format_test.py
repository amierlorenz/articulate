"""PROTOTYPE — throwaway. Does Ollama enforce `format` for qwen3:8b, with and without think / pattern? (articulate#26)

Usage: python3 ollama_format_test.py
"""
import json, urllib.request

from schema import SCHEMA

TINY = {"type": "object", "additionalProperties": False, "required": ["title", "n"],
        "properties": {"title": {"type": "string"}, "n": {"type": "integer"}}}
TINY_PAT = json.loads(json.dumps(TINY))
TINY_PAT["properties"]["title"]["pattern"] = "^[^\\t\\x08\\x0c]*$"


def call(fmt, think):
    body = {"model": "qwen3:8b", "stream": False, "format": fmt,
            "options": {"temperature": 0, "num_predict": 400},
            "messages": [{"role": "user", "content": "Name one statistics concept and a number. Reply as JSON with keys 'name' and 'value'."}]}
    if think is not None:
        body["think"] = think
    req = urllib.request.Request("http://localhost:11434/api/chat", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=600) as r:
        raw = json.load(r)
    return raw["message"]["content"][:160].replace("\n", " ")


for label, fmt in (("tiny", TINY), ("tiny+pattern", TINY_PAT), ("extract schema", SCHEMA)):
    for think in (None, False):
        try:
            print(f"{label:15s} think={think!s:5s} -> {call(fmt, think)}")
        except Exception as e:
            print(f"{label:15s} think={think!s:5s} -> ERROR {e}")
