"""
python -m digest build [--date YYYY-MM-DD] [--limit N] [--no-model] [--force | --keep-existing]
python -m digest render
"""

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from .build import DEFAULT_LIMIT, BuildError, build_edition
from .feeds import KST
from .render import ROOT, render_site

EDITIONS = ROOT / "editions"
PUBLIC = ROOT / "public"


def yesterday_kst():
    return datetime.now(KST).date() - timedelta(days=1)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m digest")
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build", help="collect one day, write editions/DATE.json and render the site")
    b.add_argument("--date", type=date.fromisoformat, default=None, help="KST date, default yesterday")
    b.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="maximum number of stories")
    b.add_argument("--no-model", action="store_true", help="skip OpenAI; rank by coverage and use feed leads")
    existing = b.add_mutually_exclusive_group()
    existing.add_argument("--force", action="store_true", help="overwrite an existing edition")
    existing.add_argument("--keep-existing", action="store_true", help="exit quietly if the edition exists")
    sub.add_parser("render", help="rebuild public/ from editions/ (no network)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.command == "render":
        dates = render_site(EDITIONS, PUBLIC)
        print(f"rendered {len(dates)} edition(s), latest {dates[0]}")
        return 0

    day = args.date or yesterday_kst()
    if day >= datetime.now(KST).date():
        parser.error("only finished days can be built (KST)")
    target = EDITIONS / f"{day.isoformat()}.json"
    if target.exists() and args.keep_existing:
        print(f"{target.relative_to(ROOT)} already exists, nothing to do")
        return 0
    if target.exists() and not args.force:
        parser.error(f"{target.relative_to(ROOT)} already exists, use --force to rebuild it")

    writer = None
    if not args.no_model:
        if not os.getenv("OPENAI_API_KEY"):
            parser.error("OPENAI_API_KEY is not set (or pass --no-model)")
        from .llm import Writer

        writer = Writer()

    try:
        edition = build_edition(day, writer=writer, limit=max(1, min(args.limit, 12)))
    except BuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    EDITIONS.mkdir(exist_ok=True)
    previous = target.read_text(encoding="utf-8") if target.exists() else None
    target.write_text(json.dumps(edition, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        render_site(EDITIONS, PUBLIC)
    except Exception:
        # Leave editions/ as it was so the build can simply be run again.
        if previous is None:
            target.unlink()
        else:
            target.write_text(previous, encoding="utf-8")
        raise
    by_model = sum(s["summary_by"] == "model" for s in edition["stories"])
    print(f"{day}: {len(edition['stories'])} stories from {edition['candidates']} candidates "
          f"({by_model} model summaries) -> {target.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
