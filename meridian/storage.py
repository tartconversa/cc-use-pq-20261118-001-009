"""证据服务的 SQLite 模式与事务边界。"""
import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path


SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS sources (
  source_id TEXT PRIMARY KEY, region TEXT NOT NULL, outlet TEXT NOT NULL,
  topics TEXT NOT NULL, audiences TEXT NOT NULL, active INTEGER NOT NULL,
  created_at TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS observations (
  origin TEXT NOT NULL, external_id TEXT NOT NULL, revision INTEGER NOT NULL,
  payload_hash TEXT NOT NULL, payload TEXT NOT NULL, story_key TEXT NOT NULL,
  source_id TEXT NOT NULL REFERENCES sources(source_id), published_at TEXT NOT NULL,
  received_at TEXT NOT NULL, status TEXT NOT NULL, reason TEXT,
  supersedes INTEGER, PRIMARY KEY(origin, external_id, revision)
);
CREATE INDEX IF NOT EXISTS observations_story ON observations(story_key, published_at);
CREATE TABLE IF NOT EXISTS observation_heads (
  origin TEXT NOT NULL, external_id TEXT NOT NULL, revision INTEGER NOT NULL,
  PRIMARY KEY(origin, external_id)
);
CREATE TABLE IF NOT EXISTS decisions (
  decision_id TEXT PRIMARY KEY, origin TEXT NOT NULL, external_id TEXT NOT NULL,
  revision INTEGER NOT NULL, reviewer TEXT NOT NULL, topic TEXT NOT NULL,
  stance TEXT NOT NULL, audience TEXT NOT NULL, reason TEXT NOT NULL,
  created_at TEXT NOT NULL, event_no INTEGER NOT NULL,
  UNIQUE(origin, external_id, revision, reviewer),
  FOREIGN KEY(origin, external_id, revision)
    REFERENCES observations(origin, external_id, revision)
);
CREATE INDEX IF NOT EXISTS decisions_observation ON decisions(origin, external_id, revision);
CREATE TABLE IF NOT EXISTS adjudications (
  case_key TEXT PRIMARY KEY, origin TEXT NOT NULL, external_id TEXT NOT NULL,
  revision INTEGER NOT NULL, adjudicator TEXT NOT NULL, topic TEXT NOT NULL,
  stance TEXT NOT NULL, audience TEXT NOT NULL, reason TEXT NOT NULL,
  decision_heads TEXT NOT NULL, created_at TEXT NOT NULL, event_no INTEGER NOT NULL,
  FOREIGN KEY(origin, external_id, revision)
    REFERENCES observations(origin, external_id, revision)
);
CREATE TABLE IF NOT EXISTS events (
  event_no INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,
  entity_key TEXT NOT NULL, payload_hash TEXT NOT NULL, occurred_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS batches (
  batch_id TEXT PRIMARY KEY, manifest_hash TEXT NOT NULL, expected_count INTEGER NOT NULL,
  state TEXT NOT NULL, cursor INTEGER NOT NULL, accepted INTEGER NOT NULL,
  duplicate_count INTEGER NOT NULL, rejected INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS batch_rows (
  batch_id TEXT NOT NULL REFERENCES batches(batch_id), row_no INTEGER NOT NULL,
  row_hash TEXT NOT NULL, status TEXT NOT NULL, error TEXT, result TEXT,
  PRIMARY KEY(batch_id, row_no)
);
CREATE TABLE IF NOT EXISTS releases (
  release_id TEXT PRIMARY KEY, query_hash TEXT NOT NULL, event_head INTEGER NOT NULL,
  preview_token TEXT NOT NULL, report_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS report_queries (
  query_hash TEXT PRIMARY KEY, spec_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS collection_frames (
  frame_id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(source_id),
  start_at TEXT NOT NULL, end_at TEXT NOT NULL, expected_count INTEGER NOT NULL,
  state TEXT NOT NULL, actual_count INTEGER NOT NULL DEFAULT 0,
  frozen_hash TEXT, created_at TEXT NOT NULL, closed_at TEXT
);
CREATE INDEX IF NOT EXISTS frames_source_window ON collection_frames(source_id,start_at,end_at);
CREATE TABLE IF NOT EXISTS frame_members (
  frame_id TEXT NOT NULL REFERENCES collection_frames(frame_id),
  origin TEXT NOT NULL, external_id TEXT NOT NULL, revision INTEGER NOT NULL,
  PRIMARY KEY(frame_id,origin,external_id),
  FOREIGN KEY(origin,external_id,revision) REFERENCES observations(origin,external_id,revision)
);
"""


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def decode(value):
    return json.loads(value)


class Store:
    def __init__(self, path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=15, isolation_level=None, check_same_thread=False)
        self.lock = threading.RLock()
        self.depth = 0
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=15000")
        self.db.executescript(SCHEMA)

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        with self.lock:
            outer = self.depth == 0
            if outer:
                self.db.execute("BEGIN IMMEDIATE")
            else:
                self.db.execute(f"SAVEPOINT meridian_{self.depth}")
            self.depth += 1
            level = self.depth - 1
            try:
                yield self.db
                self.depth -= 1
                if outer:
                    self.db.execute("COMMIT")
                else:
                    self.db.execute(f"RELEASE SAVEPOINT meridian_{level}")
            except BaseException:
                self.depth -= 1
                if outer:
                    self.db.execute("ROLLBACK")
                else:
                    self.db.execute(f"ROLLBACK TO SAVEPOINT meridian_{level}")
                    self.db.execute(f"RELEASE SAVEPOINT meridian_{level}")
                raise

    def one(self, sql, params=()):
        return self.db.execute(sql, params).fetchone()

    def all(self, sql, params=()):
        return self.db.execute(sql, params).fetchall()

    def head(self):
        row = self.one("SELECT COALESCE(MAX(event_no), 0) AS head FROM events")
        return row["head"]

    def event(self, kind, entity_key, payload_hash, occurred_at):
        cursor = self.db.execute(
            "INSERT INTO events(kind,entity_key,payload_hash,occurred_at) VALUES(?,?,?,?)",
            (kind, entity_key, payload_hash, occurred_at),
        )
        return cursor.lastrowid

