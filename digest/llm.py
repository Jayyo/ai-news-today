"""OpenAI calls for picking stories and writing summaries.

The model never supplies titles, links, outlets or dates; those come from the
feeds. It only returns candidate ids and short summaries with the source
phrases it relied on, which summarize.check() verifies.
"""

import hashlib
import json
import os
import re
from pathlib import Path

from . import select

DEFAULT_MODEL = "gpt-6.1-sol"
CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache" / "llm"
MAX_CANDIDATES = 60

PICK_INSTRUCTIONS = """You edit a short Korean daily digest of AI news.
From CANDIDATES choose up to {limit} stories that matter most to people who follow AI:
new models and products, research results, major company moves and deals, chips and
infrastructure, policy and regulation. Skip event and ceremony notices, routine
partnership announcements, local news, and stories where AI is only mentioned in passing.
Use only ids from CANDIDATES, ordered from most to least important. Returning fewer is fine."""

SUMMARY_INSTRUCTIONS = """Summarize the news in SOURCE for Korean readers in one or two sentences
of plain Korean newspaper style (~했다).
Rules:
- Use only facts stated in SOURCE. Add no background knowledge, guesses, opinions or predictions.
- Keep names, numbers and dates exactly as SOURCE writes them.
- Do not attribute anything to anyone (~라고 밝혔다, ~고 전했다) unless SOURCE does.
- In "evidence", copy word for word the phrases from SOURCE that support the summary.
- If SOURCE says too little to go beyond its headline, return status "insufficient_source"
  with empty lists."""

PICK_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["ids"],
    "properties": {"ids": {"type": "array", "items": {"type": "string"}}},
}

SUMMARY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["status", "summary", "evidence"],
    "properties": {
        "status": {"type": "string", "enum": ["ok", "insufficient_source"]},
        "summary": {"type": "array", "items": {"type": "string"}},
        "evidence": {"type": "array", "items": {"type": "string"}},
    },
}


class Writer:
    def __init__(self, model=None, client=None, cache_dir=CACHE_DIR):
        self.model = model or os.getenv("DIGEST_MODEL") or DEFAULT_MODEL
        if client is None:
            from openai import OpenAI

            client = OpenAI()  # reads OPENAI_API_KEY
        self.client = client
        self.cache_dir = cache_dir

    def _call(self, instructions, text, schema, name, max_tokens, accept=None):
        # Answers are cached by their exact input, so rebuilding a day from the same
        # feed text does not pay twice. Answers that fail `accept` are not kept.
        key = hashlib.sha256(json.dumps([self.model, instructions, text, schema]).encode()).hexdigest()
        path = self.cache_dir / f"{key[:32]}.json" if self.cache_dir else None
        if path and path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                path.unlink()
        response = self.client.responses.create(
            model=self.model,
            instructions=instructions,
            input=text,
            text={"format": {"type": "json_schema", "name": name, "schema": schema, "strict": True}},
            reasoning={"effort": "low"},
            max_output_tokens=max_tokens,
        )
        data = json.loads(response.output_text)
        if path and (accept is None or accept(data)):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return data

    def pick(self, groups, limit):
        """The chosen groups, most important first."""
        groups = select.priority(groups)[:MAX_CANDIDATES]
        lines = [
            json.dumps({"id": f"c{i}", "outlets": [a.source for a in g], "title": g[0].title,
                        "text": g[0].text[:300]}, ensure_ascii=False)
            for i, g in enumerate(groups)
        ]
        data = self._call(PICK_INSTRUCTIONS.format(limit=limit), "CANDIDATES:\n" + "\n".join(lines),
                          PICK_SCHEMA, "pick", 600)
        order = []
        for value in data.get("ids", []):
            match = re.fullmatch(r"c(\d+)", str(value).strip())
            if match and int(match.group(1)) < len(groups) and int(match.group(1)) not in order:
                order.append(int(match.group(1)))
        return [groups[i] for i in order[:limit]]

    def summarize(self, source_text, accept=None):
        return self._call(SUMMARY_INSTRUCTIONS, "SOURCE:\n" + source_text, SUMMARY_SCHEMA, "summary", 900,
                          accept=accept)
