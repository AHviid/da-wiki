"""Local HTML interface for browsing the Danish Wikipedia dump."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from dawiki.db import similar_titles
from dawiki.dump import DumpStore, find_dump, normalize_title
from dawiki.pageviews import top_articles
from dawiki.wikitext import render_wikitext, title_to_path

HERE = Path(__file__).parent
PAGE_SIZE = 50

store: DumpStore | None = None
templates = Jinja2Templates(directory=HERE / "templates")
templates.env.globals["title_to_path"] = title_to_path


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global store
    store = DumpStore.from_root()
    yield


app = FastAPI(title="da-wiki", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


def _store() -> DumpStore:
    if store is None:
        raise RuntimeError("dump store is not loaded")
    return store


@app.get("/", response_class=HTMLResponse)
async def home(request: Request, q: str = "", page: int = 1):
    dump = _store()
    page = max(1, page)
    top = None
    month = ""
    if not q.strip():
        fetched = top_articles(dump)
        if fetched:
            top, month = fetched

    if top is not None:
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "q": q,
                "top_articles": top,
                "month_label": month,
                "titles": [],
                "total": len(top),
                "page": 1,
                "page_size": PAGE_SIZE,
                "pages": 1,
                "has_prev": False,
                "has_next": False,
                "dump_name": dump.xml_path.parent.name,
            },
        )

    offset = (page - 1) * PAGE_SIZE
    titles, total = dump.search(q, offset=offset, limit=PAGE_SIZE)
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "q": q,
            "top_articles": None,
            "month_label": "",
            "titles": titles,
            "total": total,
            "page": page,
            "page_size": PAGE_SIZE,
            "pages": pages,
            "has_prev": page > 1,
            "has_next": page < pages,
            "dump_name": dump.xml_path.parent.name,
        },
    )


@app.get("/wiki/{title:path}", response_class=HTMLResponse)
async def article(request: Request, title: str):
    dump = _store()
    resolved = dump.resolve(title)
    if resolved is None:
        return templates.TemplateResponse(
            request,
            "missing.html",
            {"title": normalize_title(title)},
            status_code=404,
        )

    page = resolved.page
    wanted = normalize_title(title)
    canonical = normalize_title(page.title)
    if wanted != canonical and not resolved.redirected_from:
        return RedirectResponse(title_to_path(page.title), status_code=302)

    return templates.TemplateResponse(
        request,
        "article.html",
        {
            "title": page.title,
            "body": render_wikitext(page.wikitext),
            "redirected_from": resolved.redirected_from,
            "timestamp": page.timestamp,
            "similar": similar_titles(page.title),
        },
    )


def main() -> None:
    find_dump()
    uvicorn.run("dawiki.app:app", host="127.0.0.1", port=8000, reload=True)


if __name__ == "__main__":
    main()
