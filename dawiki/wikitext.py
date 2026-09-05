"""Convert MediaWiki wikitext to readable HTML (not a full Parsoid renderer)."""

from __future__ import annotations

import html
import re
from urllib.parse import quote

import mwparserfromhell
from mwparserfromhell.nodes import (
    Comment,
    ExternalLink,
    Heading,
    HTMLEntity,
    Tag,
    Template,
    Text,
    Wikilink,
)
from mwparserfromhell.wikicode import Wikicode

from dawiki.dump import normalize_title

_FILE_PREFIXES = ("Fil:", "File:", "Image:", "Billede:", "Media:")
_CAT_PREFIXES = ("Kategori:", "Category:")
_SAFE_TAGS = frozenset(
    {
        "b",
        "blockquote",
        "code",
        "dd",
        "dl",
        "dt",
        "em",
        "i",
        "li",
        "ol",
        "p",
        "pre",
        "s",
        "small",
        "strong",
        "sub",
        "sup",
        "u",
        "ul",
    }
)
_STRIP_TAGS = frozenset({"ref", "references", "gallery", "timeline", "math", "chem", "score"})
_BLOCK_LINE = re.compile(r"^\s*</?(?:h[1-6]|ul|ol|li|dl|dt|dd|pre|blockquote|table|figure|hr)\b", re.I)
_LIST_LINE = re.compile(r"^([*#:;]+)(.*)$")
_HR = re.compile(r"^-{4,}$")


def title_to_path(title: str) -> str:
    return "/wiki/" + quote(normalize_title(title).replace(" ", "_"), safe=":$+!'(),")


def render_wikitext(wikitext: str) -> str:
    code = mwparserfromhell.parse(wikitext)
    categories: list[str] = []
    raw = _render_code(code, categories)
    body = _format_blocks(raw)
    if categories:
        links = "".join(
            f'<li><a href="{html.escape(title_to_path("Kategori:" + c), quote=True)}">'
            f"{html.escape(c)}</a></li>"
            for c in categories
        )
        body += f'<nav class="categories"><h2>Kategorier</h2><ul>{links}</ul></nav>'
    return body


def _render_code(code: Wikicode, categories: list[str]) -> str:
    return "".join(_render_node(node, categories) for node in code.nodes)


def _render_node(node, categories: list[str]) -> str:
    if isinstance(node, Text):
        return _format_quotes(html.escape(str(node)))
    if isinstance(node, HTMLEntity):
        return html.escape(node.normalize())
    if isinstance(node, Comment) or isinstance(node, Template):
        return ""
    if isinstance(node, Heading):
        level = min(max(node.level, 1), 6)
        inner = _render_code(node.title, categories).strip()
        return f"\n<h{level}>{inner}</h{level}>\n"
    if isinstance(node, Wikilink):
        return _render_wikilink(node, categories)
    if isinstance(node, ExternalLink):
        return _render_external(node, categories)
    if isinstance(node, Tag):
        return _render_tag(node, categories)
    if isinstance(node, Wikicode):
        return _render_code(node, categories)
    return _format_quotes(html.escape(str(node)))


def _render_wikilink(node: Wikilink, categories: list[str]) -> str:
    title = str(node.title).strip()
    if title.startswith(_CAT_PREFIXES):
        categories.append(title.split(":", 1)[1].strip())
        return ""
    if title.startswith(_FILE_PREFIXES):
        caption = html.escape(title.split(":", 1)[-1])
        if node.text:
            rendered = _render_code(node.text, categories)
            skip = {"thumb", "thumbnail", "right", "left", "center", "none", "frameless", "frame", "border"}
            for part in reversed(rendered.split("|")):
                part = part.strip()
                if not part or part.lower() in skip or re.fullmatch(r"\d+px", part.lower()):
                    continue
                caption = part
                break
        return f'<figure class="file"><figcaption>{caption}</figcaption></figure>'

    href_title = title
    fragment = ""
    if "#" in title:
        href_title, fragment = title.split("#", 1)
    label = _render_code(node.text, categories).strip() if node.text else html.escape(href_title or title)
    if not href_title:
        href = f"#{quote(fragment)}"
    else:
        href = title_to_path(href_title)
        if fragment:
            href += "#" + quote(fragment)
    return f'<a href="{html.escape(href, quote=True)}">{label}</a>'


def _render_external(node: ExternalLink, categories: list[str]) -> str:
    url = html.escape(str(node.url).strip(), quote=True)
    if node.title:
        label = _render_code(node.title, categories).strip()
    else:
        label = html.escape(str(node.url).strip())
    return f'<a href="{url}" rel="nofollow">{label}</a>'


def _render_tag(node: Tag, categories: list[str]) -> str:
    name = str(node.tag).lower()
    if name in _STRIP_TAGS:
        return ""
    if name in {"br", "wbr"}:
        return "<br>"
    if name == "hr":
        return "\n<hr>\n"
    inner = _render_code(node.contents, categories) if node.contents else ""
    if name in {"nowiki", "noinclude", "includeonly"}:
        return inner
    if name in _SAFE_TAGS:
        if node.self_closing:
            return f"<{name}>"
        return f"<{name}>{inner}</{name}>"
    return inner


def _format_quotes(text: str) -> str:
    out: list[str] = []
    bold = italic = False
    i = 0
    n = len(text)
    while i < n:
        if text.startswith("'''", i):
            bold = not bold
            out.append("<b>" if bold else "</b>")
            i += 3
        elif text.startswith("''", i):
            italic = not italic
            out.append("<i>" if italic else "</i>")
            i += 2
        else:
            out.append(text[i])
            i += 1
    if bold:
        out.append("</b>")
    if italic:
        out.append("</i>")
    return "".join(out)


def _format_blocks(raw: str) -> str:
    raw = _strip_tables(raw)
    lines = raw.replace("\r\n", "\n").split("\n")
    parts: list[str] = []
    para: list[str] = []
    i = 0

    def flush_para() -> None:
        if not para:
            return
        text = " ".join(s.strip() for s in para if s.strip())
        para.clear()
        if text:
            parts.append(f"<p>{text}</p>")

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            flush_para()
            i += 1
            continue
        if _HR.match(stripped):
            flush_para()
            parts.append("<hr>")
            i += 1
            continue
        if _BLOCK_LINE.match(stripped):
            flush_para()
            parts.append(stripped)
            i += 1
            continue
        if _LIST_LINE.match(stripped):
            flush_para()
            block, i = _render_list(lines, i)
            parts.append(block)
            continue
        para.append(stripped)
        i += 1
    flush_para()
    return "\n".join(parts)


def _strip_tables(text: str) -> str:
    return re.sub(r"\{\|[\s\S]*?\|\}", "", text)


def _render_list(lines: list[str], start: int) -> tuple[str, int]:
    items: list[tuple[str, str]] = []
    i = start
    while i < len(lines):
        m = _LIST_LINE.match(lines[i].strip())
        if not m:
            break
        items.append((m.group(1), m.group(2).strip()))
        i += 1

    html_parts: list[str] = []
    stack: list[tuple[str, str]] = []  # (marker, list-tag)

    def close_to(depth: int) -> None:
        while len(stack) > depth:
            _, tag = stack.pop()
            html_parts.append(f"</{tag}>")

    for prefix, rest in items:
        depth = len(prefix)
        marker = prefix[-1]
        list_tag = {"#": "ol", "*": "ul", ":": "dl", ";": "dl"}[marker]
        item_tag = {"#": "li", "*": "li", ":": "dd", ";": "dt"}[marker]

        while stack and (
            len(stack) > depth
            or (len(stack) == depth and (stack[-1][0] != marker or stack[-1][1] != list_tag))
        ):
            _, tag = stack.pop()
            html_parts.append(f"</{tag}>")

        while len(stack) < depth:
            next_marker = prefix[len(stack)]
            next_tag = {"#": "ol", "*": "ul", ":": "dl", ";": "dl"}[next_marker]
            html_parts.append(f"<{next_tag}>")
            stack.append((next_marker, next_tag))

        html_parts.append(f"<{item_tag}>{rest}</{item_tag}>")

    close_to(0)
    return "".join(html_parts), i
