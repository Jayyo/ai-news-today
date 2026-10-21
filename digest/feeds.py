"""Fetching and parsing the RSS feeds."""

import hashlib
import html
import re
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

KST = timezone(timedelta(hours=9))
USER_AGENT = "ai-news-today/1.0 (+https://github.com/Jayyo/ai-news-today)"
MAX_FEED_BYTES = 5 * 1024 * 1024


@dataclass(frozen=True)
class Source:
    name: str
    url: str
    # Items whose description starts with one of these are not AI news
    # (for example regional desk posts that only carry an AI-made video).
    skip_prefixes: tuple = ()


SOURCES = (
    Source("AI타임스", "https://www.aitimes.com/rss/allArticle.xml", skip_prefixes=("AI 생성 영상",)),
    Source("인공지능신문", "https://www.aitimes.kr/rss/allArticle.xml"),
)


@dataclass
class Article:
    id: str
    source: str
    title: str
    url: str
    published: datetime
    author: str = ""
    text: str = ""
    skip_prefixes: tuple = field(default=(), repr=False)


def fetch(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read(MAX_FEED_BYTES + 1)
    if len(data) > MAX_FEED_BYTES:
        raise ValueError(f"feed too large: {url}")
    return data


def clean(text):
    """Unescape entities and collapse whitespace. Angle brackets stay: titles use <...> for names."""
    return re.sub(r"\s+", " ", html.unescape(text or "")).strip()


def clean_description(text):
    """Descriptions may carry markup; drop tags before unescaping."""
    text = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    # Paragraphs come glued together ("...했다.구글은"); put the space back.
    text = re.sub(r"(?<=[가-힣]\.)(?=[가-힣A-Za-z'\"‘“(])", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_date(value):
    """RFC 822 dates, or 'YYYY-MM-DD HH:MM:SS' which these Korean feeds use for KST."""
    value = (value or "").strip()
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?", value)
    if match:
        y, mo, d, h, mi, s = (int(x or 0) for x in match.groups())
        try:
            return datetime(y, mo, d, h, mi, s, tzinfo=KST)
        except ValueError:
            return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=KST)
    return parsed.astimezone(KST)


def normalize_url(url):
    try:
        parts = urlsplit((url or "").strip())
    except ValueError:  # e.g. a malformed IPv6 host
        return None
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not k.lower().startswith("utm_")]
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path, urlencode(query), ""))


def parse_feed(source, data):
    """Articles plus the number of items skipped for a missing title, link or date."""
    root = ET.fromstring(data)
    articles, skipped, seen = [], 0, set()
    for item in root.iterfind("./channel/item"):
        url = normalize_url(item.findtext("link"))
        title = clean(item.findtext("title"))
        published = parse_date(item.findtext("pubDate"))
        if not (url and title and published):
            skipped += 1  # never guess a missing date or link
            continue
        if url in seen:
            continue
        seen.add(url)
        articles.append(Article(
            id=hashlib.sha1(url.encode()).hexdigest()[:10],
            source=source.name,
            title=title,
            url=url,
            published=published,
            author=clean(item.findtext("author")),
            text=clean_description(item.findtext("description")),
            skip_prefixes=source.skip_prefixes,
        ))
    return articles, skipped


def day_window(day):
    start = datetime(day.year, day.month, day.day, tzinfo=KST)
    return start, start + timedelta(days=1)


def collect(day, sources=SOURCES, fetcher=fetch):
    """Articles published on `day` (KST) plus a per-source status report."""
    start, end = day_window(day)
    articles, report = [], []
    for source in sources:
        try:
            items, skipped = parse_feed(source, fetcher(source.url))
        except Exception as exc:  # one broken feed should not sink the edition
            report.append({"name": source.name, "url": source.url, "ok": False, "error": str(exc)[:200]})
            continue
        in_window = [a for a in items if start <= a.published < end]
        oldest = min((a.published for a in items), default=None)
        report.append({
            "name": source.name,
            "url": source.url,
            "ok": True,
            "articles": len(in_window),
            "skipped": skipped,
            # The feed only keeps its latest items; if it does not reach back to the
            # start of the day, part of that day is missing.
            "covers_whole_day": bool(oldest and oldest < start),
        })
        articles.extend(in_window)
    return articles, report
