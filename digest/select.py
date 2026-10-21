"""Filtering, grouping the same story across outlets, and ranking."""

import re

AI_TERMS = re.compile(
    r"(?<![A-Za-z])(?:AI|AGI|LLM|sLLM|GPT|GPU|TPU|NPU)(?![A-Za-z])"
    r"|인공지능|에이전트|생성형|머신러닝|딥러닝|로봇|반도체|챗봇|챗GPT|오픈AI|앤트로픽|제미나이|데이터센터|파운데이션 모델|언어모델|언어 모델",
    re.IGNORECASE,
)
QUOTE_CHARS = re.compile(r"[‘’“”\"]")
QUOTED = re.compile(r"'([^']{2,30})'")


def is_candidate(article):
    if any(article.text.startswith(prefix) for prefix in article.skip_prefixes):
        return False
    if article.title.startswith("["):
        return False  # bulletins, book columns and newsletter recaps
    return bool(AI_TERMS.search(article.title + " " + article.text))


def _bigrams(text):
    s = re.sub(r"[^0-9a-z가-힣]", "", text.lower())
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _quoted(text):
    return set(QUOTED.findall(QUOTE_CHARS.sub("'", text)))


def same_story(a, b):
    """Titles from different outlets that describe the same event.

    Tuned on real pairs, e.g. "오픈AI, 신약 개발 관리 플랫폼 '오케스트라 바이오' 인수"
    vs "오픈AI, 신약 개발 조율하는 AI 에이전트 만든다…신약 개발 플랫폼 '오케스트라 바이오' 인수".
    """
    if a.source == b.source:
        return False
    ba, bb = _bigrams(a.title), _bigrams(b.title)
    overlap = len(ba & bb) / max(1, len(ba | bb))
    return overlap >= 0.3 or (overlap >= 0.15 and bool(_quoted(a.title) & _quoted(b.title)))


def group(articles):
    """Merge articles that cover the same story, at most one per outlet.

    The one-per-outlet rule also stops chains (A~B, B~C) from pulling two
    different stories of the same outlet into one group.
    """
    parent = list(range(len(articles)))
    outlets = [{a.source} for a in articles]

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(articles)):
        for j in range(i + 1, len(articles)):
            ri, rj = find(i), find(j)
            if ri != rj and not (outlets[ri] & outlets[rj]) and same_story(articles[i], articles[j]):
                parent[ri] = rj
                outlets[rj] |= outlets[ri]

    groups = {}
    for i, article in enumerate(articles):
        groups.setdefault(find(i), []).append(article)
    result = []
    for members in groups.values():
        # Lead with the most detailed description; ties go to the earliest article.
        members.sort(key=lambda a: (-len(a.text), a.published))
        result.append(members)
    result.sort(key=lambda g: min(a.published for a in g))
    return result


def priority(groups):
    """Stories covered by more outlets first, then the most recent."""
    return sorted(groups, key=lambda g: (len({a.source for a in g}), max(a.published for a in g)), reverse=True)


def default_rank(groups, limit):
    return priority(groups)[:limit]
