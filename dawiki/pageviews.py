"""Wikimedia monthly top-views for da.wikipedia."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import date, timedelta

import httpx

from dawiki.dump import DumpStore, is_main_namespace, normalize_title

UA = "dawiki-browser/0.1 (contact: ballebyhviid@hotmail.com)"
API = (
    "https://wikimedia.org/api/rest_v1/metrics/pageviews/top/"
    "da.wikipedia/all-access/{year}/{month}/all-days"
)
CACHE_TTL = 24 * 3600
HOME_LIMIT = 100

_SKIP_EXACT = frozenset({"Forside", "wiki.phtml"})
_SKIP_PREFIXES = (
    "Speciel:",
    "Fil:",
    "File:",
    "Billede:",
    "Wikipedia:",
    "Hjælp:",
    "Bruger:",
    "Media:",
    "Kategori:",
    "Skabelon:",
    "Portal:",
    "Modul:",
    "MediaWiki:",
)

_MONTHS_DA = (
    "",
    "januar",
    "februar",
    "marts",
    "april",
    "maj",
    "juni",
    "juli",
    "august",
    "september",
    "oktober",
    "november",
    "december",
)

_cache: tuple[float, str, list["TopArticle"]] | None = None


@dataclass(frozen=True)
class TopArticle:
    title: str
    views: int
    rank: int


def default_month() -> tuple[str, str]:
    raw = os.environ.get("DAWIKI_TOPVIEWS_MONTH", "").strip()
    if raw:
        year, month = raw.split("-", 1)
        return year, month.zfill(2)
    first = date.today().replace(day=1)
    prev = first - timedelta(days=1)
    return f"{prev.year:04d}", f"{prev.month:02d}"


def month_label(year: str, month: str) -> str:
    return f"{_MONTHS_DA[int(month)]} {year}"


def _skip_title(title: str) -> bool:
    if title in _SKIP_EXACT:
        return True
    if any(title.startswith(p) for p in _SKIP_PREFIXES):
        return True
    return not is_main_namespace(title)


def _fetch(year: str, month: str) -> list[tuple[str, int, int]]:
    url = API.format(year=year, month=month)
    with httpx.Client(headers={"User-Agent": UA}, timeout=20, follow_redirects=True) as c:
        r = c.get(url)
        r.raise_for_status()
        items = r.json()["items"][0]["articles"]
    out: list[tuple[str, int, int]] = []
    for row in items:
        title = normalize_title(row["article"].replace("_", " "))
        if _skip_title(title):
            continue
        out.append((title, int(row["views"]), int(row["rank"])))
    return out


def top_articles(dump: DumpStore, *, limit: int = HOME_LIMIT) -> tuple[list[TopArticle], str] | None:
    global _cache
    year, month = default_month()
    label = month_label(year, month)
    now = time.monotonic()
    if _cache is not None:
        ts, cached_label, rows = _cache
        if cached_label == label and now - ts < CACHE_TTL:
            return rows, label

    try:
        raw = _fetch(year, month)
    except Exception:
        return None

    picked: list[TopArticle] = []
    for title, views, rank in raw:
        if dump.lookup(title) is None:
            continue
        picked.append(TopArticle(title, views, rank))
        if len(picked) >= limit:
            break

    _cache = (now, label, picked)
    return picked, label
