"""Summaries checked against the feed text, with the publisher's lead as fallback."""

import logging
import re

log = logging.getLogger(__name__)

NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
MAX_SUMMARY_CHARS = 320


def source_text(group):
    return "\n\n".join(f"[{a.source}] {a.title}\n{a.text}" for a in group)


def _norm(text):
    text = re.sub(r"[‘’`]", "'", text or "")
    text = re.sub(r"[“”]", '"', text)
    text = text.replace("…", "...")
    return re.sub(r"\s+", " ", text).strip()


def _numbers(text):
    return {n.replace(",", "") for n in NUMBER.findall(text)}


def check(result, source):
    """Why a model result can't be used, or None if it passes.

    This only proves that the quoted phrases and every number in the summary
    occur in the source. It does not prove the summary means the same thing.
    """
    if not isinstance(result, dict) or result.get("status") != "ok":
        return "model reported insufficient source"
    summary, evidence = result.get("summary"), result.get("evidence")
    if not all(isinstance(x, list) and all(isinstance(v, str) for v in x) for x in (summary, evidence)):
        return "summary and evidence must be lists of strings"
    sentences = [s.strip() for s in summary if s.strip()]
    if not sentences or len(sentences) > 2:
        return "expected one or two sentences"
    if len(" ".join(sentences)) > MAX_SUMMARY_CHARS:
        return "summary too long"
    quotes = [q for q in (_norm(e).strip("'\"「」『』<> ") for e in evidence) if q]
    if not quotes:
        return "no evidence given"
    src = _norm(source)
    for quote in quotes:
        if quote not in src:
            return f"evidence not found in source: {quote[:40]}"
    missing = _numbers(" ".join(sentences)) - _numbers(src)
    if missing:
        return f"number not in source: {sorted(missing)[0]}"
    return None


def lead(text, limit=220):
    """The first sentence or two of the publisher's description."""
    text = (text or "").strip()
    sentences = re.split(r"(?<=[다.!?])\s+", text)
    out = ""
    for sentence in sentences:
        if out and len(out) + 1 + len(sentence) > limit:
            break
        out = f"{out} {sentence}".strip()
    if len(out) > limit:
        out = out[:limit].rstrip() + "…"
    return out


def summarize_group(group, writer=None):
    text = source_text(group)
    if writer is not None:
        try:
            result = writer.summarize(text, accept=lambda r: check(r, text) is None)
            problem = check(result, text)
        except Exception as exc:  # network, quota or schema errors: fall back to the lead
            result, problem = None, f"model call failed: {exc}"
        if problem is None:
            return {
                "summary": [s.strip() for s in result["summary"] if s.strip()],
                "summary_by": "model",
                "evidence": result["evidence"],
            }
        log.warning("Using the feed lead for %r: %s", group[0].title[:40], problem)
    return {"summary": [lead(group[0].text)], "summary_by": "feed", "evidence": []}
