import sqlite3
from datetime import datetime, date
import json

DB_PATH = "quiz_records.db"

def _now():
    return datetime.utcnow().isoformat(timespec="seconds")

def _today_utc():
    return date.today().isoformat()  # YYYY-MM-DD

def get_conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)

def init_db():
    conn = get_conn()
    cur = conn.cursor()

    # attempts：一次提交=一次考试
    cur.execute("""
    CREATE TABLE IF NOT EXISTS attempts (
        attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_key TEXT NOT NULL,           -- 唯一用户标识（建议员工号；没有则姓名|部门）
        user_name TEXT,
        department TEXT,
        employee_id TEXT,
        systems_json TEXT,                -- 本次考试范围(JSON)
        score INTEGER NOT NULL,
        passed INTEGER NOT NULL,          -- 1/0
        attempt_date TEXT NOT NULL,       -- YYYY-MM-DD（用于“一天一次”限制）
        started_at TEXT NOT NULL,
        submitted_at TEXT NOT NULL
    )
    """)

    # 答题明细（便于导出/核对）
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

    # 用户已做过题（用于“避免重复抽题”）
    cur.execute("""
    CREATE TABLE IF NOT EXISTS user_seen (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_key TEXT NOT NULL,
        question_id TEXT NOT NULL,
        first_seen_at TEXT NOT NULL,
        UNIQUE(user_key, question_id)
    )
    """)

    conn.commit()
    conn.close()

def has_attempt_on_date(user_key: str, attempt_date: str) -> bool:
    """同一用户同一天是否已提交过"""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT 1 FROM attempts
        WHERE user_key=? AND attempt_date=?
        LIMIT 1
    """, (user_key, attempt_date))
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

def mark_seen(user_key: str, question_ids: list[str]):
    conn = get_conn()
    cur = conn.cursor()
    ts = _now()
    for qid in question_ids:
        cur.execute(
            "INSERT OR IGNORE INTO user_seen(user_key, question_id, first_seen_at) VALUES (?, ?, ?)",
            (user_key, qid, ts)
        )
    conn.commit()
    conn.close()

def get_seen_set(user_key: str) -> set[str]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT question_id FROM user_seen WHERE user_key=?", (user_key,))
    rows = cur.fetchall()
    conn.close()
    return set(r[0] for r in rows)

def save_attempt(user_info: dict, systems: list[str], score: int, passed: bool,
                 attempt_date: str, started_at: str, submitted_at: str, answer_rows: list[dict]) -> int:
    """
    answer_rows: [{question_id, system, type, user_answer, correct_answer, is_correct}]
    """
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

def list_attempts(user_key: str, limit: int = 20):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
    SELECT attempt_id, score, passed, attempt_date, started_at, submitted_at, systems_json
    FROM attempts
    WHERE user_key=?
    ORDER BY attempt_id DESC
    LIMIT ?
    """, (user_key, limit))
    rows = cur.fetchall()
    conn.close()
    return rows

def get_attempt_answers(attempt_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
    SELECT question_id, question_system, question_type, user_answer, correct_answer, is_correct
    FROM attempt_answers
    WHERE attempt_id=?
    ORDER BY id ASC
    """, (attempt_id,))
    rows = cur.fetchall()
    conn.close()
    return rows

def reset_seen(user_key: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM user_seen WHERE user_key=?", (user_key,))
    conn.commit()
    conn.close()

def today_utc():
    return _today_utc()