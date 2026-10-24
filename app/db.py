import os
import sqlite3
from contextlib import contextmanager

DB_PATH = os.getenv("DB_PATH", "/data/shop.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  phone TEXT UNIQUE NOT NULL,
  email TEXT,
  pw_hash TEXT NOT NULL,
  created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS orders(
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  gb INTEGER NOT NULL,
  amount INTEGER NOT NULL,                 -- toman
  receipt TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',  -- pending|approved|rejected
  note TEXT,
  created_at INTEGER NOT NULL,
  decided_at INTEGER
);
CREATE TABLE IF NOT EXISTS services(
  user_id INTEGER PRIMARY KEY REFERENCES users(id),
  sub_id TEXT NOT NULL,
  client_uuid TEXT NOT NULL,
  clients TEXT NOT NULL,                   -- JSON [[inbound_id, email], ...]
  total_gb INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',   -- active|revoked
  created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY, actor TEXT, action TEXT, target TEXT, at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS ix_orders_user ON orders(user_id);
"""


@contextmanager
def conn():
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    try:
        yield c
        c.commit()
    except BaseException:
        c.rollback()
        raise
    finally:
        c.close()


def init():
    with conn() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)
