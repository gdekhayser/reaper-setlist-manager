"""SQLite database: songs, setlists and the songs in each setlist."""
import os
import sqlite3
from contextlib import contextmanager

from config import APP_DIR

DB_PATH = os.path.join(APP_DIR, "setlists.db")

MOODS = ["Ballad", "Classic Rock", "80's", "Country", "Dance"]
EFFORTS = ["Low", "Medium", "High"]


def _sql_list(values):
    return ", ".join("'" + v.replace("'", "''") + "'" for v in values)


SCHEMA = f"""
CREATE TABLE IF NOT EXISTS songs (
    id              INTEGER PRIMARY KEY,
    rel_path        TEXT NOT NULL UNIQUE COLLATE NOCASE,  -- relative to project_root
    title           TEXT NOT NULL,
    original_artist TEXT NOT NULL DEFAULT '',
    mood            TEXT CHECK (mood IS NULL OR mood IN ({_sql_list(MOODS)})),
    voice_effort    TEXT CHECK (voice_effort IS NULL OR voice_effort IN ({_sql_list(EFFORTS)})),
    detected_length REAL,             -- seconds, from the end of the last item in the .rpp
    length_override REAL,             -- seconds, set by hand; wins over detected_length
    hidden          INTEGER NOT NULL DEFAULT 0,
    missing         INTEGER NOT NULL DEFAULT 0,  -- file not found on the last scan
    notes           TEXT NOT NULL DEFAULT '',
    auto_info       TEXT,             -- how classifier.py arrived at its suggestions
    file_mtime      REAL,
    last_scanned    TEXT
);

CREATE TABLE IF NOT EXISTS setlists (
    id             INTEGER PRIMARY KEY,
    name           TEXT NOT NULL UNIQUE COLLATE NOCASE,
    target_minutes REAL NOT NULL DEFAULT 45,
    mood           TEXT,              -- NULL means mixed
    notes          TEXT NOT NULL DEFAULT '',
    rpl_file       TEXT,              -- absolute path of the .rpl this setlist writes to
    created_at     TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at     TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS setlist_songs (
    setlist_id INTEGER NOT NULL REFERENCES setlists(id) ON DELETE CASCADE,
    position   INTEGER NOT NULL,
    song_id    INTEGER NOT NULL REFERENCES songs(id),
    PRIMARY KEY (setlist_id, position)
);
"""


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def session():
    """Connection that commits on success, rolls back on error, and always closes."""
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init():
    with session() as conn:
        conn.executescript(SCHEMA)
        # Databases created before auto_info existed.
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(songs)")}
        if "auto_info" not in columns:
            conn.execute("ALTER TABLE songs ADD COLUMN auto_info TEXT")
