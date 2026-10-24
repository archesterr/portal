import os
import sqlite3
from contextlib import contextmanager

DB_PATH = os.getenv("DB_PATH", "/data/portal.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY,
  phone TEXT UNIQUE NOT NULL,
  full_name TEXT,
  national_code TEXT UNIQUE,
  kyc_status TEXT NOT NULL DEFAULT 'none',   -- none|submitted|approved|rejected
  kyc_note TEXT,
  id_doc TEXT,
  selfie TEXT,
  created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS otps(
  phone TEXT PRIMARY KEY,
  code_hash TEXT NOT NULL,
  expires_at INTEGER NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  last_sent INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS requests(
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  plan_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',    -- pending|active|rejected|revoked
  note TEXT,
  sub_id TEXT,
  client_uuid TEXT,
  clients TEXT,                              -- JSON [[inbound_id, email], ...]
  created_at INTEGER NOT NULL,
  decided_at INTEGER
);
CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY,
  actor TEXT, action TEXT, target TEXT,
  at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_req_user ON requests(user_id);
CREATE INDEX IF NOT EXISTS ix_req_status ON requests(status);
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
