#!/usr/bin/env python3
"""Embed main-namespace dump articles into Postgres + pgvector."""

from __future__ import annotations

import argparse
import re
import sys
import time

import mwparserfromhell
from tqdm import tqdm

from dawiki.db import connect, ensure_index, ensure_schema, existing_ids, upsert_embeddings
from dawiki.dump import DumpStore, Page

MODEL_NAME = "intfloat/multilingual-e5-small"
LEAD_CHARS = 1500
MIN_CHARS = 50
DEFAULT_BATCH = 32


def _device() -> str:
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
        if torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


def _plain_lead(wikitext: str) -> str:
    text = mwparserfromhell.parse(wikitext).strip_code()
    text = re.sub(r"\s+", " ", text).strip()
    return text[:LEAD_CHARS]


def _connect_ready(attempts: int = 20):
    last: Exception | None = None
    for _ in range(attempts):
        try:
            return connect()
        except Exception as exc:
            last = exc
            time.sleep(1)
    print(
        "cannot connect to Postgres at 127.0.0.1:5432\n"
        "the container often exits if Docker's disk is full\n"
        "  docker compose logs db\n"
        "  docker compose up -d --wait",
        file=sys.stderr,
    )
    raise SystemExit(last)


def _eligible(page: Page) -> str | None:
    if page.ns != 0 or page.redirect_to:
        return None
    lead = _plain_lead(page.wikitext)
    if len(lead) < MIN_CHARS:
        return None
    return lead


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--batch-size", type=int, default=DEFAULT_BATCH)
    p.add_argument("--limit", type=int, help="embed at most this many new pages (debug)")
    a = p.parse_args()

    dump = DumpStore.from_root()
    print(f"dump: {dump.xml_path.parent.name}  streams: {len(dump.offsets)}")

    conn = _connect_ready()
    ensure_schema(conn)
    done = existing_ids(conn)
    print(f"already embedded: {len(done)}")

    from sentence_transformers import SentenceTransformer

    device = _device()
    print(f"model: {MODEL_NAME}  device: {device}")
    model = SentenceTransformer(MODEL_NAME, device=device)

    batch_ids: list[int] = []
    batch_titles: list[str] = []
    batch_texts: list[str] = []
    added = skipped = 0

    def flush() -> None:
        nonlocal added
        if not batch_ids:
            return
        vectors = model.encode(
            [f"passage: {t}" for t in batch_texts],
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        upsert_embeddings(
            conn,
            [
                (page_id, title, vec.tolist())
                for page_id, title, vec in zip(batch_ids, batch_titles, vectors)
            ],
        )
        added += len(batch_ids)
        batch_ids.clear()
        batch_titles.clear()
        batch_texts.clear()

    try:
        for offset in tqdm(dump.offsets, desc="streams"):
            try:
                pages = dump.pages_at_offset(offset)
            except Exception as exc:
                print(f"\nskip stream {offset}: {exc}", file=sys.stderr)
                continue
            for page in pages:
                if page.page_id in done:
                    skipped += 1
                    continue
                lead = _eligible(page)
                if lead is None:
                    continue
                batch_ids.append(page.page_id)
                batch_titles.append(page.title)
                batch_texts.append(lead)
                done.add(page.page_id)
                if len(batch_ids) >= a.batch_size:
                    flush()
                if a.limit is not None and added + len(batch_ids) >= a.limit:
                    flush()
                    raise StopIteration
    except StopIteration:
        pass

    flush()
    print("building hnsw index (first time can take a while)...")
    ensure_index(conn)
    conn.close()
    print(f"embedded {added} new pages  skipped-existing {skipped}")


if __name__ == "__main__":
    main()
