"""Turning one day of feed items into an edition (a JSON document)."""

import logging
from datetime import datetime

from . import feeds, select
from .summarize import summarize_group

log = logging.getLogger(__name__)
DEFAULT_LIMIT = 6


class BuildError(RuntimeError):
    pass


def _ref(article):
    return {"title": article.title, "url": article.url, "source": article.source,
            "published": article.published.isoformat()}


def build_edition(day, writer=None, limit=DEFAULT_LIMIT, fetcher=feeds.fetch, sources=feeds.SOURCES):
    articles, report = feeds.collect(day, sources=sources, fetcher=fetcher)
    if not any(r["ok"] for r in report):
        raise BuildError("every feed failed: " + "; ".join(r.get("error", "") for r in report))

    candidates = [a for a in articles if select.is_candidate(a)]
    if not candidates:
        raise BuildError(f"no AI stories found for {day}")
    groups = select.group(candidates)

    picked = None
    if writer is not None:
        try:
            picked = writer.pick(groups, limit)
        except Exception as exc:
            log.warning("Model ranking failed, using the default order: %s", exc)
        if picked == []:
            raise BuildError("the model did not pick any story; not writing an empty edition")
    if picked is None:
        picked = select.default_rank(groups, limit)

    stories = []
    for members in picked:
        lead = members[0]
        stories.append({
            **_ref(lead),
            "author": lead.author,
            **summarize_group(members, writer),
            "also": [_ref(a) for a in members[1:]],
        })

    return {
        "date": day.isoformat(),
        "generated_at": datetime.now(feeds.KST).isoformat(timespec="seconds"),
        "model": writer.model if writer is not None else None,
        "sources": report,
        "articles": len(articles),
        "candidates": len(groups),
        "stories": stories,
    }
