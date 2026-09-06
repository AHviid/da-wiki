# da-wiki

Browse a local Danish Wikipedia article dump in the browser.

## Download a dump

```bash
uv run scripts/bulk_download.py
```

Files land in `dumps/YYYYMMDD/`.

## Run the browser

```bash
uv sync
uv run uvicorn dawiki.app:app --reload
```

Then open http://127.0.0.1:8000

The home page lists the most-viewed da.wikipedia articles for the last completed month (Wikimedia Pageviews API). Search still filters local titles. Override the month with `DAWIKI_TOPVIEWS_MONTH=2026-08`.

The app reads the newest dump under `dumps/` (or `DAWIKI_DUMP_DIR`). The `.xml.bz2` file stays compressed; articles are decompressed on demand from the multistream index.

## Similar articles (optional)

Nearest-neighbor links need Postgres with pgvector and a one-time embed of the dump.

```bash
docker compose up -d --wait
uv sync --extra embed
uv run scripts/embed_dump.py
```

`--wait` fails if Postgres never becomes healthy (the usual cause is Docker Desktop running out of VM disk). Data lives in `data/pgdata` on the host, not in a Docker volume.

Uses `intfloat/multilingual-e5-small` (384-d). The first run downloads the model and can take hours on CPU; it skips pages already in the table. Point at another database with `DATABASE_URL` (default `postgresql://dawiki:dawiki@127.0.0.1:5432/dawiki`).

Without Postgres, articles still render; the similar-article block is hidden.
