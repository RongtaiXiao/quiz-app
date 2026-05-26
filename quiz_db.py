import sqlite3
from datetime import datetime, date
import json
import random

DB_PATH = "quiz_records.db"

def _now():
    return datetime.utcnow().isoformat(timespec="seconds")

def today_utc():
    return date.today().isoformat()  # YYYY-MM-DD

def get_conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)

def init_db():
    conn = get_conn()
    cur = conn.cursor()

    # 1) 答题记录
    cur.execute("""
    CREATE TABLE IF NOT EXISTS attempts (
        attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_key TEXT NOT NULL,
        user_name TEXT,
        department TEXT,
        employee_id TEXT,
        systems_json TEXT,
        score INTEGER NOT NULL,
        passed INTEGER NOT NULL,
        attempt_date TEXT NOT NULL,
        started_at TEXT NOT NULL,
        submitted_at TEXT NOT NULL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS attempt_answers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        attempt_id INTEGER NOT NULL,
        question_id TEXT NOT NULL,
        question_system TEXT,
        question_type TEXT,
        user_answer TEXT,
        correct_answer TEXT,
        is_correct INTEGER NOT NULL,
        FOREIGN KEY(attempt_id) REFERENCES attempts(attempt_id)
    )
    """)

    # 2) 避免重复抽题
    cur.execute("""
    CREATE TABLE IF NOT EXISTS user_seen (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_key TEXT NOT NULL,
        question_id TEXT NOT NULL,
        first_seen_at TEXT NOT NULL,
        UNIQUE(user_key, question_id)
    )
    """)

    # 3) 抽奖库存（限量）
    cur.execute("""
    CREATE TABLE IF NOT EXISTS prize_inventory (
        tier TEXT PRIMARY KEY,
        total INTEGER NOT NULL,
        remaining INTEGER NOT NULL
    )
    """)

    # 4) 中奖记录（attempt_id UNIQUE：一次答题只抽一次）
    cur.execute("""
    CREATE TABLE IF NOT EXISTS prize_wins (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        attempt_id INTEGER NOT NULL UNIQUE,
        user_key TEXT NOT NULL,
        user_name TEXT,
        department TEXT,
        employee_id TEXT,
        tier TEXT NOT NULL,
        prize_name TEXT NOT NULL,
        win_time TEXT NOT NULL
    )
    """)

    conn.commit()
    conn.close()

# ---------- 一天一次 ----------
def has_attempt_on_date(user_key: str, attempt_date: str) -> bool:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM attempts WHERE user_key=? AND attempt_date=? LIMIT 1", (user_key, attempt_date))
    ok = cur.fetchone() is not None
    conn.close()
    return ok

def get_latest_attempt_on_date(user_key: str, attempt_date: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT attempt_id, score, passed, submitted_at, systems_json
        FROM attempts
        WHERE user_key=? AND attempt_date=?
        ORDER BY attempt_id DESC
        LIMIT 1
    """, (user_key, attempt_date))
    row = cur.fetchone()
    conn.close()
    return row

# ---------- 保存/查询答题 ----------
def save_attempt(user_info: dict, systems: list[str], score: int, passed: bool,
                 attempt_date: str, started_at: str, submitted_at: str, answer_rows: list[dict]) -> int:
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    INSERT INTO attempts(user_key, user_name, department, employee_id, systems_json, score, passed, attempt_date, started_at, submitted_at)
    VALUES(?,?,?,?,?,?,?,?,?,?)
    """, (
        user_info["user_key"],
        user_info.get("name", ""),
        user_info.get("department", ""),
        user_info.get("employee_id", ""),
        json.dumps(systems, ensure_ascii=False),
        score,
        1 if passed else 0,
        attempt_date,
        started_at,
        submitted_at
    ))
    attempt_id = cur.lastrowid

    for r in answer_rows:
        cur.execute("""
        INSERT INTO attempt_answers(attempt_id, question_id, question_system, question_type, user_answer, correct_answer, is_correct)
        VALUES(?,?,?,?,?,?,?)
        """, (
            attempt_id,
            r["question_id"],
            r.get("system", ""),
            r.get("type", ""),
            str(r.get("user_answer", "")),
            str(r.get("correct_answer", "")),
            1 if r.get("is_correct") else 0
        ))

    conn.commit()
    conn.close()
    return attempt_id

def list_attempts(user_key: str, limit: int = 2000):
