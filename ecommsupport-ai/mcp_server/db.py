"""Thin MySQL access layer for the MCP server.

Every statement in this project goes through `query()` / `execute()` with
`%s` placeholders, so values are always sent to the driver as bound
parameters and never interpolated into SQL text. There is deliberately no
function that accepts raw SQL from a caller outside this package.
"""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import pymysql
from dotenv import load_dotenv
from pymysql.cursors import DictCursor

# The server runs as its own process, so it loads the project's .env itself rather than
# relying on whoever launched it. Values already set in the environment win.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

_local = threading.local()


def _connect() -> pymysql.connections.Connection:
    return pymysql.connect(
        host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.getenv("MYSQL_USER", "novashop_mcp"),
        password=os.getenv("MYSQL_PASSWORD", "change-me"),
        database=os.getenv("MYSQL_DATABASE", "novashop"),
        cursorclass=DictCursor,
        autocommit=True,
        charset="utf8mb4",
        connect_timeout=5,
        read_timeout=10,
        write_timeout=10,
    )


def get_connection() -> pymysql.connections.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None or not conn.open:
        conn = _connect()
        _local.conn = conn
    else:
        conn.ping(reconnect=True)
    return conn


def query(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with get_connection().cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def query_one(sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    rows = query(sql, params)
    return rows[0] if rows else None


@contextmanager
def transaction() -> Iterator[pymysql.cursors.Cursor]:
    """Run several parameterised statements atomically."""
    conn = get_connection()
    conn.begin()
    cur = conn.cursor()
    try:
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
