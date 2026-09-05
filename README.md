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

The app reads the newest dump under `dumps/` (or `DAWIKI_DUMP_DIR`). The `.xml.bz2` file stays compressed; articles are decompressed on demand from the multistream index.
