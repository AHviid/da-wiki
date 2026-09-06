"""Postgres + pgvector access. Optional: the app runs without a database."""

from __future__ import annotations

import os

import psycopg
from pgvector.psycopg import register_vector

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://dawiki:dawiki@127.0.0.1:5432/dawiki",
)
EMBEDDING_DIM = 384


def connect() -> psycopg.Connection:
    conn = psycopg.connect(DATABASE_URL, connect_timeout=2)
    register_vector(conn)
    return conn


def ensure_schema(conn: psycopg.Connection) -> None:
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS articles (
          page_id INTEGER PRIMARY KEY,
          title TEXT NOT NULL UNIQUE,
          embedding vector({EMBEDDING_DIM}) NOT NULL
        )
        """
    )
    conn.commit()


def ensure_index(conn: psycopg.Connection) -> None:
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS articles_embedding_hnsw
        ON articles USING hnsw (embedding vector_cosine_ops)
        """
    )
    conn.commit()


def existing_ids(conn: psycopg.Connection) -> set[int]:
    with conn.cursor() as cur:
        cur.execute("SELECT page_id FROM articles")
        return {row[0] for row in cur}


def upsert_embeddings(
    conn: psycopg.Connection,
    rows: list[tuple[int, str, list[float]]],
) -> None:
    if not rows:
        return
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO articles (page_id, title, embedding)
            VALUES (%s, %s, %s)
            ON CONFLICT (page_id) DO UPDATE
              SET title = EXCLUDED.title, embedding = EXCLUDED.embedding
            """,
            rows,
        )
    conn.commit()


def similar_titles(title: str, *, limit: int = 8) -> list[str]:
    try:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT page_id, embedding FROM articles WHERE title = %s",
                    (title,),
                )
                row = cur.fetchone()
                if row is None:
                    return []
                page_id, embedding = row
                cur.execute(
                    """
                    SELECT title
                    FROM articles
                    WHERE page_id != %s
                    ORDER BY embedding <=> %s
                    LIMIT %s
                    """,
                    (page_id, embedding, limit),
                )
                return [r[0] for r in cur]
    except Exception:
        return []
