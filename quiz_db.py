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

    # ========= 考试记录 =========
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
        attempt_date TEXT NOT NULL,     -- YYYY-MM-DD（UTC）
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

    # ========= 避免重复抽题：记录已做题 =========
    cur.execute("""
    CREATE TABLE IF NOT EXISTS user_seen (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_key TEXT NOT NULL,
        question_id TEXT NOT NULL,
        first_seen_at TEXT NOT NULL,
        UNIQUE(user_key, question_id)
    )
    """)

    # ========= 抽奖库存（限量） =========
    cur.execute("""
    CREATE TABLE IF NOT EXISTS prize_inventory (
        tier TEXT PRIMARY KEY,       -- 一等奖/二等奖/三等奖
        total INTEGER NOT NULL,
        remaining INTEGER NOT NULL
    )
    """)

    # ========= 中奖记录（attempt_id唯一=每次考试只抽一次） =========
    cur.execute("""
    CREATE TABLE IF NOT EXISTS prize_wins (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        attempt_id INTEGER NOT NULL UNIQUE,
        user_key TEXT NOT NULL,
        user_name TEXT,
        department TEXT,
        employee_id TEXT,
        tier TEXT NOT NULL,          -- 一等奖/二等奖/三等奖/未中奖
        prize_name TEXT NOT NULL,    -- 可写具体奖品名
        win_time TEXT NOT NULL
    )
    """)

    conn.commit()
    conn.close()

# ---------------- 一天一次提交限制 ----------------
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

# ---------------- 考试保存/查询 ----------------
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

# ---------------- 避免重复抽题：已做题 ----------------
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

def reset_seen(user_key: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM user_seen WHERE user_key=?", (user_key,))
    conn.commit()
    conn.close()

# ---------------- 抽奖库存初始化/查询 ----------------
def seed_prize_inventory_if_empty(prize_config: dict):
    """
    prize_config:
    {
      "一等奖": {"total": 10, "prize_name": "一等奖"},
      "二等奖": {"total": 20, "prize_name": "二等奖"},
      "三等奖": {"total": 50, "prize_name": "三等奖"}
    }
    仅在库存表为空时初始化一次；不会每次重置库存。
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM prize_inventory")
    cnt = cur.fetchone()[0]
    if cnt == 0:
        for tier, cfg in prize_config.items():
            total = int(cfg["total"])
            cur.execute(
                "INSERT INTO prize_inventory(tier, total, remaining) VALUES (?, ?, ?)",
                (tier, total, total)
            )
        conn.commit()
    conn.close()

def get_prize_inventory():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT tier, total, remaining FROM prize_inventory")
    rows = cur.fetchall()
    conn.close()
    # rows: [(tier,total,remaining)]
    return rows

# ---------------- 抽奖记录查询/写入（限量 + 不超发） ----------------
def get_win_by_attempt(attempt_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT tier, prize_name, win_time
        FROM prize_wins
        WHERE attempt_id=?
        LIMIT 1
    """, (attempt_id,))
    row = cur.fetchone()
    conn.close()
    return row  # None or (tier, prize_name, win_time)

def list_wins(limit: int = 200):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT win_time, user_name, department, employee_id, tier, prize_name, attempt_id
        FROM prize_wins
        ORDER BY id DESC
        LIMIT ?
    """, (limit,))
    rows = cur.fetchall()
    conn.close()
    return rows

def draw_prize_once(attempt_id: int, user_info: dict, prize_config: dict, weights: dict):
    """
    核心保证：
    1) 同一个 attempt 只能抽一次（attempt_id UNIQUE）
    2) 不会超发（BEGIN IMMEDIATE 写锁 + remaining>0 原子扣减）
    3) 若库存全空 → 记录“未中奖”
    """
    existing = get_win_by_attempt(attempt_id)
    if existing:
        return {"tier": existing[0], "prize_name": existing[1], "win_time": existing[2], "already": True}

    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")  # 拿写锁，避免并发超发
        cur = conn.cursor()

        # 并发双检
        cur.execute("SELECT tier, prize_name, win_time FROM prize_wins WHERE attempt_id=? LIMIT 1", (attempt_id,))
        row = cur.fetchone()
        if row:
            conn.commit()
            return {"tier": row[0], "prize_name": row[1], "win_time": row[2], "already": True}

        # 可用库存
        cur.execute("SELECT tier, remaining FROM prize_inventory WHERE remaining > 0")
        available = cur.fetchall()  # [(tier, remaining), ...]

        if not available:
            win_time = _now()
            cur.execute("""
                INSERT INTO prize_wins(attempt_id, user_key, user_name, department, employee_id, tier, prize_name, win_time)
                VALUES (?,?,?,?,?,?,?,?)
            """, (attempt_id, user_info["user_key"], user_info.get("name",""),
                  user_info.get("department",""), user_info.get("employee_id",""),
                  "未中奖", "未中奖", win_time))
            conn.commit()
            return {"tier": "未中奖", "prize_name": "未中奖", "win_time": win_time, "already": False}

        # 仅从有库存的tier里抽
        tiers = [t for (t, r) in available]
        tier_weights = [weights.get(t, 1) for t in tiers]
        picked_tier = random.choices(tiers, weights=tier_weights, k=1)[0]
        prize_name = prize_config.get(picked_tier, {}).get("prize_name", picked_tier)

        # 扣库存（原子）
        cur.execute("""
            UPDATE prize_inventory
            SET remaining = remaining - 1
            WHERE tier = ? AND remaining > 0
        """, (picked_tier,))
        if cur.rowcount != 1:
            # 极端并发下扣减失败：记未中奖（最稳策略）
            win_time = _now()
            cur.execute("""
                INSERT INTO prize_wins(attempt_id, user_key, user_name, department, employee_id, tier, prize_name, win_time)
                VALUES (?,?,?,?,?,?,?,?)
            """, (attempt_id, user_info["user_key"], user_info.get("name",""),
                  user_info.get("department",""), user_info.get("employee_id",""),
                  "未中奖", "未中奖", win_time))
            conn.commit()
            return {"tier": "未中奖", "prize_name": "未中奖", "win_time": win_time, "already": False}

        # 写中奖记录
        win_time = _now()
        cur.execute("""
            INSERT INTO prize_wins(attempt_id, user_key, user_name, department, employee_id, tier, prize_name, win_time)
            VALUES (?,?,?,?,?,?,?,?)
        """, (attempt_id, user_info["user_key"], user_info.get("name",""),
              user_info.get("department",""), user_info.get("employee_id",""),
              picked_tier, prize_name, win_time))

        conn.commit()
        return {"tier": picked_tier, "prize_name": prize_name, "win_time": win_time, "already": False}
    except:
        conn.rollback()
        raise
    finally:
        conn.close()