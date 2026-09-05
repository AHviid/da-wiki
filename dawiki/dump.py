"""Read pages from a Wikimedia pages-articles-multistream dump."""

from __future__ import annotations

import bz2
import os
import re
import xml.etree.ElementTree as ET
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

DUMP_ROOT = Path("dumps")
INDEX_NAME = "dawiki-{date}-pages-articles-multistream-index.txt.bz2"
XML_NAME = "dawiki-{date}-pages-articles-multistream.xml.bz2"

# Content namespaces that appear in dawiki pages-articles dumps.
# Titles like "Star Wars: ..." are main-namespace and must not be filtered.
_NAMESPACE_PREFIXES = frozenset(
    {
        "Billede",
        "Bruger",
        "Fil",
        "File",
        "Hjælp",
        "Image",
        "Kategori",
        "Media",
        "MediaWiki",
        "Modul",
        "Portal",
        "Skabelon",
        "Speciel",
        "Wikipedia",
    }
)


@dataclass(frozen=True)
class IndexRecord:
    offset: int
    page_id: int
    title: str


@dataclass(frozen=True)
class Page:
    title: str
    page_id: int
    ns: int
    wikitext: str
    redirect_to: str | None
    timestamp: str | None


@dataclass(frozen=True)
class ResolvedPage:
    page: Page
    redirected_from: str | None


def normalize_title(title: str) -> str:
    t = re.sub(r"\s+", " ", title.replace("_", " ")).strip()
    if not t:
        return t
    return t[0].upper() + t[1:]


def is_main_namespace(title: str) -> bool:
    if ":" not in title:
        return True
    return title.split(":", 1)[0] not in _NAMESPACE_PREFIXES


def find_dump(root: Path | None = None) -> tuple[Path, Path]:
    root = root or Path(os.environ.get("DAWIKI_DUMP_DIR", DUMP_ROOT))
    if not root.is_dir():
        raise FileNotFoundError(f"dump directory not found: {root}")

    dates = sorted(
        (p for p in root.iterdir() if p.is_dir() and p.name.isdigit()),
        reverse=True,
    )
    for d in dates:
        xml = d / XML_NAME.format(date=d.name)
        idx = d / INDEX_NAME.format(date=d.name)
        if xml.exists() and idx.exists():
            return xml, idx
    raise FileNotFoundError(f"no dawiki multistream dump found under {root}")


def _decompress_stream(xml_path: Path, offset: int) -> str:
    with xml_path.open("rb") as f:
        f.seek(offset)
        dec = bz2.BZ2Decompressor()
        parts: list[bytes] = []
        while not dec.eof:
            chunk = f.read(1 << 16)
            if not chunk:
                parts.append(dec.decompress(b""))
                break
            parts.append(dec.decompress(chunk))
    return b"".join(parts).decode("utf-8")


def _parse_pages(fragment: str) -> dict[str, Page]:
    wrapped = f"<pages>{fragment}</pages>"
    root = ET.fromstring(wrapped)
    pages: dict[str, Page] = {}
    for el in root.findall("page"):
        title = el.findtext("title")
        if not title:
            continue
        redirect_el = el.find("redirect")
        redirect_to = redirect_el.get("title") if redirect_el is not None else None
        rev = el.find("revision")
        text_el = rev.find("text") if rev is not None else None
        pages[title] = Page(
            title=title,
            page_id=int(el.findtext("id") or 0),
            ns=int(el.findtext("ns") or 0),
            wikitext=text_el.text or "" if text_el is not None else "",
            redirect_to=redirect_to,
            timestamp=rev.findtext("timestamp") if rev is not None else None,
        )
    return pages


class DumpStore:
    def __init__(self, xml_path: Path, index_path: Path, *, stream_cache_size: int = 16):
        self.xml_path = xml_path
        self.index_path = index_path
        self._by_title: dict[str, IndexRecord] = {}
        self.main_titles: list[str] = []
        self._stream_cache: OrderedDict[int, dict[str, Page]] = OrderedDict()
        self._stream_cache_size = stream_cache_size
        self._load_index()

    @classmethod
    def from_root(cls, root: Path | None = None) -> DumpStore:
        xml, idx = find_dump(root)
        return cls(xml, idx)

    def _load_index(self) -> None:
        main: list[str] = []
        with bz2.open(self.index_path, "rt", encoding="utf-8") as f:
            for line in f:
                offset_s, page_id_s, title = line.rstrip("\n").split(":", 2)
                rec = IndexRecord(int(offset_s), int(page_id_s), title)
                self._by_title[normalize_title(title)] = rec
                if is_main_namespace(title):
                    main.append(title)
        main.sort(key=str.casefold)
        self.main_titles = main

    def lookup(self, title: str) -> IndexRecord | None:
        return self._by_title.get(normalize_title(title))

    def _pages_at(self, offset: int) -> dict[str, Page]:
        cached = self._stream_cache.get(offset)
        if cached is not None:
            self._stream_cache.move_to_end(offset)
            return cached
        pages = _parse_pages(_decompress_stream(self.xml_path, offset))
        self._stream_cache[offset] = pages
        if len(self._stream_cache) > self._stream_cache_size:
            self._stream_cache.popitem(last=False)
        return pages

    def get_page(self, title: str) -> Page | None:
        rec = self.lookup(title)
        if rec is None:
            return None
        pages = self._pages_at(rec.offset)
        return pages.get(rec.title) or pages.get(normalize_title(title))

    def resolve(self, title: str, *, max_hops: int = 8) -> ResolvedPage | None:
        seen: set[str] = set()
        redirected_from: str | None = None
        current = title
        for _ in range(max_hops):
            key = normalize_title(current)
            if key in seen:
                break
            seen.add(key)
            page = self.get_page(current)
            if page is None:
                return None
            if not page.redirect_to:
                return ResolvedPage(page, redirected_from)
            redirected_from = redirected_from or page.title
            current = page.redirect_to
        page = self.get_page(current)
        return ResolvedPage(page, redirected_from) if page else None

    def search(self, q: str, *, offset: int = 0, limit: int = 50) -> tuple[list[str], int]:
        q = q.strip().casefold()
        if not q:
            matches = self.main_titles
        else:
            prefix: list[str] = []
            rest: list[str] = []
            for title in self.main_titles:
                folded = title.casefold()
                if folded.startswith(q):
                    prefix.append(title)
                elif q in folded:
                    rest.append(title)
            matches = prefix + rest
        return matches[offset : offset + limit], len(matches)
