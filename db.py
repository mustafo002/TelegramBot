import sqlite3
import threading
from datetime import datetime, timezone
from config import DATA_DIR

DB_PATH = DATA_DIR / "bot.sqlite3"
_LOCK = threading.RLock()

def _connect():
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    with _LOCK, _connect() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            created_at TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            jobs INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            url TEXT NOT NULL,
            title TEXT,
            kind TEXT,
            quality TEXT,
            status TEXT,
            created_at TEXT NOT NULL
        );
        """)

def touch_user(user):
    now = datetime.now(timezone.utc).isoformat()
    with _LOCK, _connect() as con:
        con.execute("""
        INSERT INTO users(user_id, username, first_name, created_at, last_seen)
        VALUES(?,?,?,?,?)
        ON CONFLICT(user_id) DO UPDATE SET
          username=excluded.username,
          first_name=excluded.first_name,
          last_seen=excluded.last_seen
        """, (
            user.id,
            getattr(user, "username", None),
            getattr(user, "first_name", None),
            now, now
        ))

def increment_jobs(user_id):
    with _LOCK, _connect() as con:
        con.execute("UPDATE users SET jobs=jobs+1 WHERE user_id=?", (user_id,))

def add_history(user_id, url, title, kind, quality, status):
    with _LOCK, _connect() as con:
        con.execute("""
        INSERT INTO history(user_id,url,title,kind,quality,status,created_at)
        VALUES(?,?,?,?,?,?,?)
        """, (
            user_id, url, title, kind, quality, status,
            datetime.now(timezone.utc).isoformat()
        ))

def get_history(user_id, limit=10):
    with _LOCK, _connect() as con:
        return con.execute("""
        SELECT * FROM history WHERE user_id=?
        ORDER BY id DESC LIMIT ?
        """, (user_id, limit)).fetchall()

def stats():
    with _LOCK, _connect() as con:
        users = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        jobs = con.execute("SELECT COALESCE(SUM(jobs),0) FROM users").fetchone()[0]
        return users, jobs
