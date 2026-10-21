import json
import re
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digest import feeds, render, select  # noqa: E402
from digest.build import BuildError, build_edition  # noqa: E402
from digest.summarize import check, lead, summarize_group  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
SOURCES = (
    feeds.Source("Outlet A", "https://a.example.com/rss", skip_prefixes=("AI 생성 영상",)),
    feeds.Source("Outlet B", "https://b.example.com/rss"),
)
DAY = date(2026, 10, 21)


def fixture_fetcher(url):
    return (FIXTURES / {"https://a.example.com/rss": "outlet_a.xml", "https://b.example.com/rss": "outlet_b.xml"}[url]).read_bytes()


class FakeWriter:
    model = "test-model"

    def __init__(self, summaries=None, order=None, fail=False):
        self.summaries = summaries or {}
        self.order = order
        self.fail = fail

    def pick(self, groups, limit):
        if self.fail:
            raise RuntimeError("quota")
        order = self.order if self.order is not None else range(len(groups))
        return [groups[i] for i in order][:limit]

    def summarize(self, source_text, accept=None):
        if self.fail:
            raise RuntimeError("quota")
        for key, result in self.summaries.items():
            if key in source_text:
                return result
        return {"status": "insufficient_source", "summary": [], "evidence": []}


def test_parse_date_formats():
    assert feeds.parse_date("2026-10-21 17:57:53").isoformat() == "2026-10-21T17:57:53+09:00"
    assert feeds.parse_date("Wed, 21 Oct 2026 08:31:00 GMT").isoformat() == "2026-10-21T17:31:00+09:00"
    assert feeds.parse_date("Wed, 21 Oct 2026 14:18:00 +0900").isoformat() == "2026-10-21T14:18:00+09:00"
    assert feeds.parse_date("") is None
    assert feeds.parse_date("어제") is None
    assert feeds.parse_date("2026-13-40 10:00:00") is None


def test_normalize_url_drops_tracking_and_fragments():
    assert feeds.normalize_url("https://A.example.com/x?idxno=1&utm_source=rss#top") == "https://a.example.com/x?idxno=1"
    assert feeds.normalize_url("javascript:alert(1)") is None
    assert feeds.normalize_url("http://[::1") is None
    assert feeds.normalize_url("") is None


def test_cleaning_keeps_titles_and_strips_description_markup():
    assert feeds.clean("차카대 &lt;일상 속 AI&gt;  수업") == "차카대 <일상 속 AI> 수업"
    assert feeds.clean_description("발표했다.구글은 4.5배 빠르다고 <b>밝혔다</b>.") == "발표했다. 구글은 4.5배 빠르다고 밝혔다 ."


def test_parse_feed_skips_bad_items_without_dropping_the_feed():
    data = (FIXTURES / "outlet_a.xml").read_text(encoding="utf-8").replace(
        "</channel>",
        "<item><title>깨진 링크 AI 기사</title><link>http://[::1</link><pubDate>2026-10-21 10:00:00</pubDate></item>"
        "<item><title>같은 기사 AI 재전송</title><link>https://a.example.com/news/articleView.html?idxno=102</link>"
        "<pubDate>2026-10-21 18:00:00</pubDate></item></channel>")
    articles, skipped = feeds.parse_feed(SOURCES[0], data.encode())
    assert skipped == 2  # the undated fixture item and the broken link
    assert [a.url for a in articles].count("https://a.example.com/news/articleView.html?idxno=102") == 1


def test_collect_uses_kst_day_and_skips_undated_items():
    articles, report = feeds.collect(DAY, sources=SOURCES, fetcher=fixture_fetcher)
    titles = [a.title for a in articles]
    assert "어제 밤에 나온 AI 칩 소식" not in titles      # 2026-10-20 23:59:59 KST
    assert "자정 직후 올라온 AI 기사" not in titles        # 2026-10-22 00:00 KST
    assert "날짜 없는 AI 기사" not in titles
    assert "차카대, 전공 불문 <일상 속 AI> 수업 개설" in titles
    assert [r["articles"] for r in report] == [5, 2]
    assert all(r["ok"] for r in report)


def test_collect_reports_a_broken_feed_and_keeps_the_rest():
    def fetcher(url):
        if "b.example" in url:
            raise OSError("timed out")
        return fixture_fetcher(url)

    articles, report = feeds.collect(DAY, sources=SOURCES, fetcher=fetcher)
    assert articles and report[1]["ok"] is False and "timed out" in report[1]["error"]


def test_candidates_skip_regional_posts_bulletins_and_non_ai_news():
    articles, _ = feeds.collect(DAY, sources=SOURCES, fetcher=fixture_fetcher)
    kept = {a.title for a in articles if select.is_candidate(a)}
    assert "해변 마을 문화축제 23일 개막" not in kept
    assert "[게시판] 자차, AI 협력 등 단신" not in kept
    assert "올리브밭 위 태양광 발전소 착공" not in kept
    assert "마바전자, 초당 1000토큰 내는 언어모델 '사아 3' 공개" in kept


def test_same_story_across_outlets_is_grouped():
    articles, _ = feeds.collect(DAY, sources=SOURCES, fetcher=fixture_fetcher)
    groups = select.group([a for a in articles if select.is_candidate(a)])
    merged = [g for g in groups if len(g) > 1]
    assert len(merged) == 1
    assert {a.source for a in merged[0]} == {"Outlet A", "Outlet B"}
    assert len(groups) == 3


def test_grouping_keeps_one_article_per_outlet():
    def art(source, title, hour):
        return feeds.Article(id=title, source=source, title=title, url=f"https://x/{title}",
                             published=feeds.parse_date(f"2026-10-21 {hour:02d}:00:00"))
    a1 = art("A", "가나랩스 '다라 바이오' 인수 발표", 9)
    b1 = art("B", "가나랩스 '다라 바이오' 인수…신약 플랫폼", 10)
    a2 = art("A", "가나랩스 '다라 바이오' 인수 후속 인터뷰", 11)
    groups = select.group([a1, b1, a2])
    assert sorted(len(g) for g in groups) == [1, 2]


def test_check_rejects_unsupported_quotes_and_numbers():
    source = "가나랩스가 다라 바이오를 인수했다. 인수 금액은 300억원이다."
    good = {"status": "ok", "summary": ["가나랩스가 다라 바이오를 300억원에 인수했다."],
            "evidence": ["“가나랩스가 다라 바이오를 인수했다.”", "인수 금액은 300억원이다."]}
    assert check(good, source) is None
    assert "number" in check({**good, "summary": ["가나랩스가 다라 바이오를 500억원에 인수했다."]}, source)
    assert "number" in check({**good, "summary": ["가나랩스가 다라 바이오를 30억원에 인수했다."]}, source)
    assert "evidence" in check({**good, "evidence": ["가나랩스가 상장했다."]}, source)
    assert check({**good, "evidence": []}, source) == "no evidence given"
    assert check({**good, "evidence": ["''", " "]}, source) == "no evidence given"
    assert check({**good, "summary": "가나랩스가 인수했다."}, source) == "summary and evidence must be lists of strings"
    assert check({**good, "summary": ["a.", "b.", "c."]}, source) == "expected one or two sentences"
    assert check({"status": "insufficient_source", "summary": [], "evidence": []}, source)


def test_check_does_not_catch_meaning_changes():
    # Documented limit: real phrases plus a reworded claim still pass.
    source = "가나랩스가 다라 바이오를 인수했다."
    twisted = {"status": "ok", "summary": ["다라 바이오가 가나랩스를 인수했다."], "evidence": ["다라 바이오를 인수했다."]}
    assert check(twisted, source) is None


def test_lead_stops_at_sentence_boundaries():
    text = "첫 문장이다. 둘째 문장이다. 셋째 문장이다."
    assert lead(text, limit=16) == "첫 문장이다. 둘째 문장이다."
    assert lead(text, limit=15) == "첫 문장이다."
    assert lead("마침표 없이 아주 길게 이어지는 설명" * 20, limit=30).endswith("…")


def test_summarize_group_falls_back_to_the_feed_lead():
    articles, _ = feeds.collect(DAY, sources=SOURCES, fetcher=fixture_fetcher)
    group = [a for a in articles if "사아 3" in a.title]
    bad = FakeWriter({"사아 3": {"status": "ok", "summary": ["사아 3은 초당 5000토큰을 낸다."], "evidence": ["사아 3"]}})
    result = summarize_group(group, bad)
    assert result["summary_by"] == "feed"
    assert result["summary"] == ["마바전자는 20일(현지시간) 초당 1000토큰 이상을 생성하는 언어모델 '사아 3'를 출시했다. 가격은 이전 세대의 절반이다."]
    assert summarize_group(group, FakeWriter(fail=True))["summary_by"] == "feed"


def test_build_and_render(tmp_path):
    writer = FakeWriter({
        "사아 3": {"status": "ok", "summary": ["마바전자가 초당 1000토큰 이상을 생성하는 언어모델 '사아 3'를 출시했다."],
                 "evidence": ["초당 1000토큰 이상을 생성하는 언어모델 '사아 3'를 출시했다."]},
    }, order=[2, 0])  # groups are in publish order: 가나랩스 (14:18), 차카대 (17:31), 사아 3 (17:51)
    edition = build_edition(DAY, writer=writer, limit=2, fetcher=fixture_fetcher, sources=SOURCES)

    assert edition["date"] == "2026-10-21" and edition["model"] == "test-model"
    assert edition["candidates"] == 3
    first, second = edition["stories"]
    assert "사아 3" in first["title"] and first["summary_by"] == "model"
    assert second["summary_by"] == "feed"  # the fake model has nothing for this one
    assert [a["source"] for a in second["also"]] == ["Outlet A"]
    assert all(re.match(r"https://[ab]\.example\.com/", s["url"]) for s in edition["stories"])

    editions = tmp_path / "editions"
    editions.mkdir()
    (editions / "2026-10-21.json").write_text(json.dumps(edition, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "public"
    assert render.render_site(editions, out) == ["2026-10-21"]
    page = (out / "index.html").read_text(encoding="utf-8")
    assert page == (out / "2026-10-21.html").read_text(encoding="utf-8")
    assert "2026년 10월 21일 (수)" in page
    assert 'href="https://a.example.com/news/articleView.html?idxno=102"' in page
    assert (out / "style.css").exists()


def test_render_escapes_feed_text(tmp_path):
    edition = build_edition(DAY, writer=None, limit=5, fetcher=fixture_fetcher, sources=SOURCES)
    editions = tmp_path / "editions"
    editions.mkdir()
    (editions / "2026-10-21.json").write_text(json.dumps(edition, ensure_ascii=False), encoding="utf-8")
    render.render_site(editions, tmp_path / "public")
    page = (tmp_path / "public" / "index.html").read_text(encoding="utf-8")
    assert "&lt;일상 속 AI&gt;" in page and "<일상 속 AI>" not in page
    assert "요약 대신" in page  # no model: leads only, and the footer says so


def test_build_refuses_an_empty_day():
    with pytest.raises(BuildError):
        build_edition(date(2026, 10, 19), fetcher=fixture_fetcher, sources=SOURCES)


def test_model_picking_nothing_is_not_overridden():
    with pytest.raises(BuildError):
        build_edition(DAY, writer=FakeWriter(order=[]), fetcher=fixture_fetcher, sources=SOURCES)
    edition = build_edition(DAY, writer=FakeWriter(fail=True), limit=2, fetcher=fixture_fetcher, sources=SOURCES)
    assert len(edition["stories"]) == 2  # a failing model falls back to the default order


def test_render_does_not_link_other_schemes_and_drops_removed_editions(tmp_path):
    edition = build_edition(DAY, writer=None, limit=1, fetcher=fixture_fetcher, sources=SOURCES)
    edition["stories"][0]["url"] = "javascript:alert(1)"
    editions = tmp_path / "editions"
    editions.mkdir()
    (editions / "2026-10-21.json").write_text(json.dumps(edition, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "public"
    out.mkdir()
    (out / "2026-10-01.html").write_text("old", encoding="utf-8")
    render.render_site(editions, out)
    page = (out / "index.html").read_text(encoding="utf-8")
    assert "javascript:" not in page
    assert not (out / "2026-10-01.html").exists()
