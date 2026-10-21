"""Static HTML for every edition in editions/."""

import json
import re
import shutil
from datetime import date, datetime
from html import escape
from pathlib import Path
from string import Template

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "templates"
WEEKDAYS = "월화수목금토일"
DATE_FILE = re.compile(r"\d{4}-\d{2}-\d{2}\.(json|html)")
REPO_URL = "https://github.com/Jayyo/ai-news-today"


def load_editions(editions_dir):
    editions = []
    for path in Path(editions_dir).glob("*.json"):
        edition = json.loads(path.read_text(encoding="utf-8"))
        if path.stem != edition.get("date") or not DATE_FILE.fullmatch(path.name):
            raise ValueError(f"{path.name}: file name and date do not match")
        editions.append(edition)
    return sorted(editions, key=lambda e: e["date"], reverse=True)


def date_label(iso):
    d = date.fromisoformat(iso)
    return f"{d.year}년 {d.month}월 {d.day}일 ({WEEKDAYS[d.weekday()]})"


def _time(iso):
    t = datetime.fromisoformat(iso)
    return f"{t.month}월 {t.day}일 {t:%H:%M}"


def _link(ref, text):
    if not str(ref.get("url", "")).startswith(("https://", "http://")):
        return escape(text)  # editions are plain JSON; never turn other schemes into links
    return f'<a href="{escape(ref["url"])}" target="_blank" rel="noopener">{escape(text)}</a>'


def render_story(story, model_used):
    also = ""
    if story["also"]:
        links = ", ".join(_link(a, a["source"]) for a in story["also"])
        also = f'\n      <p class="also">함께 보도: {links}</p>'
    note = ' <span class="note" title="모델 요약 대신 기사 설명문 앞부분을 실었습니다">기사 리드</span>' \
        if story["summary_by"] == "feed" and model_used else ""
    author = f' · {escape(story["author"])}' if story.get("author") else ""
    return f"""    <article class="story">
      <p class="meta"><span class="outlet">{escape(story["source"])}</span>
        <time datetime="{escape(story["published"])}">{_time(story["published"])}</time>{author}</p>
      <h2>{_link(story, story["title"])}</h2>
      <p class="summary">{escape(" ".join(story["summary"]))}{note}</p>{also}
    </article>"""


def _archive_item(edition, current):
    attr = ' aria-current="page"' if current else ""
    return f'      <li><a href="{edition["date"]}.html"{attr}>{date_label(edition["date"])}</a></li>'


def render_page(edition, editions):
    names = [s["name"] for s in edition["sources"] if s.get("ok")]
    count = len(edition["stories"])
    archive = "\n".join(_archive_item(e, current=e["date"] == edition["date"]) for e in editions)
    if edition.get("model"):
        method = (f"기사 선택과 요약은 {escape(edition['model'])}이 RSS의 제목과 설명문만 보고 했습니다. "
                  "요약에 쓴 인용 구절이나 숫자가 설명문에 없거나 모델 호출이 실패한 기사는 설명문 앞부분을 그대로 실었습니다.")
    else:
        method = "모델 없이 만든 판이라 여러 매체가 다룬 순서대로 고르고, 요약 대신 각 기사 RSS 설명문의 앞부분을 실었습니다."
    partial = [s["name"] for s in edition["sources"] if s.get("ok") and not s.get("covers_whole_day", True)]
    missing = [s["name"] for s in edition["sources"] if not s.get("ok")]
    warnings = []
    if partial:
        warnings.append(f"{', '.join(partial)} 피드에는 이날 기사 일부만 남아 있었습니다.")
    if missing:
        warnings.append(f"{', '.join(missing)} 피드를 받지 못했습니다.")
    template = Template((TEMPLATES / "page.html").read_text(encoding="utf-8"))
    return template.substitute(
        title=escape(f"오늘의 AI 뉴스 · {date_label(edition['date'])}"),
        date_label=escape(date_label(edition["date"])),
        intro=escape(f"{' · '.join(names)}에 이날 실린 기사 {edition['articles']}건 가운데 AI 관련 "
                     f"{edition['candidates']}건(같은 사건은 하나로 셈)에서 {count}건을 골랐습니다."),
        warnings="".join(f'\n    <p class="warning">{escape(w)}</p>' for w in warnings),
        stories="\n".join(render_story(s, bool(edition.get("model"))) for s in edition["stories"]),
        archive=archive,
        method=method,
        generated=escape(edition["generated_at"][:16].replace("T", " ")),
        repo=REPO_URL,
    )


def render_site(editions_dir, out_dir):
    editions = load_editions(editions_dir)
    if not editions:
        raise SystemExit(f"no editions in {editions_dir}")
    pages = {f"{e['date']}.html": render_page(e, editions) for e in editions}
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.html"):
        if DATE_FILE.fullmatch(old.name) and old.name not in pages:
            old.unlink()  # the edition was removed
    shutil.copyfile(TEMPLATES / "style.css", out / "style.css")
    for name, html in pages.items():
        (out / name).write_text(html, encoding="utf-8")
    (out / "index.html").write_text(pages[f"{editions[0]['date']}.html"], encoding="utf-8")
    return [e["date"] for e in editions]
