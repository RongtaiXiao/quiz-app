import json
import random
import time
from datetime import datetime
from io import BytesIO

import pandas as pd
import streamlit as st

import quiz_db

st.set_page_config(page_title="随机抽题考试", layout="wide")
st.title("🎯 随机抽题考试（抽5题 / 自动评分 / ≥80分可转盘抽奖）")

# ---------------- 配置：考试与抽奖 ----------------
QUESTIONS_PER_QUIZ = 5
POINTS_PER_Q = 20
PASS_SCORE = 80

# ✅ 奖品配置：把 XX 改成真实数量
PRIZE_CONFIG = {
    "一等奖": {"total": 13, "prize_name": "一等奖"},
    "二等奖": {"total": 20, "prize_name": "二等奖"},
    "三等奖": {"total": 100, "prize_name": "三等奖"},
}
# 权重（可调）：权重越大越容易抽中（前提是该奖项还有库存）
PRIZE_WEIGHTS = {"一等奖": 1, "二等奖": 3, "三等奖": 6}

# ---------------- 初始化DB ----------------
quiz_db.init_db()
quiz_db.seed_prize_inventory_if_empty(PRIZE_CONFIG)

# ---------------- 读取题库 ----------------
@st.cache_data
def load_bank(path="question_bank.json"):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

bank = load_bank()
all_questions = bank["questions"]

# ---------------- 用户信息 ----------------
st.subheader("👤 考生信息（用于成绩/中奖记录）")
col1, col2, col3 = st.columns(3)
name = col1.text_input("姓名（必填）", value=st.session_state.get("name", ""))
dept = col2.text_input("部门（可选）", value=st.session_state.get("dept", ""))
empid = col3.text_input("员工编号（建议填写，用于唯一识别/一天一次）", value=st.session_state.get("empid", ""))

st.session_state["name"] = name
st.session_state["dept"] = dept
st.session_state["empid"] = empid

if not name.strip():
    st.warning("请先填写姓名再开始考试。")
    st.stop()

user_key = empid.strip() if empid.strip() else f"{name.strip()}|{dept.strip()}"
user_info = {"user_key": user_key, "name": name.strip(), "department": dept.strip(), "employee_id": empid.strip()}

today = quiz_db.today_utc()

# ---------------- 一天一次提交限制 ----------------
already = quiz_db.has_attempt_on_date(user_key, today)
latest_today = quiz_db.get_latest_attempt_on_date(user_key, today) if already else None
if already:
    st.warning(f"⛔ 你今天（{today}，UTC）已提交过一次考试，为避免重复刷分，今天不能再次提交。")
    if latest_today:
        attempt_id, score, passed, submitted_at, _ = latest_today
        st.info(f"今天最新一次：Attempt ID={attempt_id}，得分={score}，是否通过={'✅' if passed==1 else '❌'}，提交时间={submitted_at}")

# ---------------- 题库范围筛选（可选） ----------------
systems = sorted(list({q["system"] for q in all_questions}))
selected_systems = st.multiselect("选择题库范围（不选=全部）", systems, default=[])
candidate = [q for q in all_questions if (not selected_systems or q["system"] in selected_systems)]

st.caption(f"当前可用题目数：{len(candidate)}")
if len(candidate) < QUESTIONS_PER_QUIZ:
    st.error("题库题目少于5题，无法抽题。请调整筛选。")
    st.stop()

# ---------------- 避免重复抽题 ----------------
seen_set = quiz_db.get_seen_set(user_key)

def pick_questions_without_repeat(pool, seen, k):
    unseen = [q for q in pool if q["id"] not in seen]
    if len(unseen) >= k:
        return random.sample(unseen, k), 0
    picked = unseen[:]
    need = k - len(picked)
    seen_pool = [q for q in pool if q["id"] in seen]
    picked += random.sample(seen_pool, need)
    return picked, need

# ---------------- 生成试卷 ----------------
if "quiz" not in st.session_state:
    st.session_state.started_at = datetime.utcnow().isoformat(timespec="seconds")
    st.session_state.quiz, st.session_state.repeat_fill_count = pick_questions_without_repeat(candidate, seen_set, QUESTIONS_PER_QUIZ)
    st.session_state.submitted = False
    st.session_state.last_attempt_id = None

c1, c2 = st.columns(2)
if c1.button("🔄 重新抽题（新一套5题）"):
    st.session_state.started_at = datetime.utcnow().isoformat(timespec="seconds")
    st.session_state.quiz, st.session_state.repeat_fill_count = pick_questions_without_repeat(candidate, seen_set, QUESTIONS_PER_QUIZ)
    st.session_state.submitted = False
    st.session_state.last_attempt_id = None
    st.rerun()

if c2.button("♻️ 清空我的做题历史（重新开始无重复抽题）"):
    quiz_db.reset_seen(user_key)
    st.success("已清空历史做题记录，请重新抽题。")
    st.rerun()

quiz = st.session_state.quiz
repeat_fill = st.session_state.get("repeat_fill_count", 0)
if repeat_fill > 0:
    st.info(f"题库中你未做过的题不足5题，本次有 {repeat_fill} 题从已做过题中补齐。")

# ---------------- 答题区 ----------------
st.subheader("📝 答题区（共5题）")
user_answers = []

for i, q in enumerate(quiz, start=1):
    st.markdown(f"### 第 {i} 题（{q['system']}）")

    if q["type"] == "single_choice":
        labels = [f"{k}. {v}" for k, v in q["options"].items()]
        choice = st.radio(q["question"], labels, key=f"q{i}")
        user_answers.append(choice.split(".", 1)[0])

    elif q["type"] == "true_false":
        tf_labels = ["√（正确）", "×（错误）"]
        choice = st.radio(q["question"], tf_labels, key=f"q{i}")
        user_answers.append(True if choice.startswith("√") else False)
    else:
        user_answers.append(None)

# ---------------- 提交评分 ----------------
submit_disabled = already
if st.button("✅ 提交并评分", disabled=submit_disabled):
    started_at = st.session_state.get("started_at", datetime.utcnow().isoformat(timespec="seconds"))
    submitted_at = datetime.utcnow().isoformat(timespec="seconds")
    attempt_date = today

    score = 0
    answer_rows = []
    question_ids = []

    for idx, q in enumerate(quiz):
        correct = q["answer"]
        ua = user_answers[idx]
        is_right = (ua == correct)
        if is_right:
            score += POINTS_PER_Q

        question_ids.append(q["id"])
        answer_rows.append({
            "question_id": q["id"],
            "system": q["system"],
            "type": q["type"],
            "user_answer": ua,
            "correct_answer": correct,
            "is_correct": is_right
        })

    passed = score >= PASS_SCORE

    attempt_id = quiz_db.save_attempt(
        user_info=user_info,
        systems=selected_systems,
        score=score,
        passed=passed,
        attempt_date=attempt_date,
        started_at=started_at,
        submitted_at=submitted_at,
        answer_rows=answer_rows
    )
    quiz_db.mark_seen(user_key, question_ids)

    st.session_state.submitted = True
    st.session_state.last_score = score
    st.session_state.last_attempt_id = attempt_id
    st.session_state.last_details = answer_rows
    st.rerun()

# ---------------- 展示评分结果 ----------------
if st.session_state.get("submitted"):
    score = st.session_state.last_score
    attempt_id = st.session_state.last_attempt_id

    st.success(f"🎯 本次得分：{score}/100（Attempt ID: {attempt_id}）")
    st.dataframe(st.session_state.last_details, use_container_width=True, hide_index=True)

    # ========== 通过后抽奖（限量奖池） ==========
    if score >= PASS_SCORE:
        st.success("✅ 恭喜通过（≥80分），可以参加转盘抽奖！")
        st.caption("规则：每次通过考试可抽奖1次；奖品数量有限，抽完即止；结果会记录在系统中，请截图用于兑奖核对。")

        # 显示库存
        inv_rows = quiz_db.get_prize_inventory()
        inv_show = [{"奖项": t, "剩余": r, "总数": tot} for (t, tot, r) in inv_rows]
        st.markdown("### 🎁 当前奖品库存（剩余/总数）")
        st.table(inv_show)

        # 已抽过则直接显示
        existing = quiz_db.get_win_by_attempt(int(attempt_id))
        if existing:
            tier, prize_name, win_time = existing
            st.info(f"你已抽过奖：{tier}（{prize_name}） | 时间：{win_time} UTC")
        else:
            if st.button("🎡 开始抽奖（转盘）"):
                result = quiz_db.draw_prize_once(
                    attempt_id=int(attempt_id),
                    user_info=user_info,
                    prize_config=PRIZE_CONFIG,
                    weights=PRIZE_WEIGHTS
                )
                if result["tier"] == "未中奖":
                    st.warning("🍀 很遗憾：未中奖（可能奖品已抽完或概率未中）")
                else:
                    st.success(f"🎉 恭喜中奖：{result['tier']}（{result['prize_name']}）")
                st.caption("请截图保存用于兑奖核对。")
                st.rerun()
    else:
        st.error("❌ 未通过（<80分），请复习后再次参加考试。")

# ---------------- 我的考试记录（最近10次） ----------------
st.subheader("📚 我的考试记录（最近10次）")
rows = quiz_db.list_attempts(user_key, limit=10)
if rows:
    hist = []
    for (aid, sc, passed, attempt_date, started_at, submitted_at, systems_json) in rows:
        try:
            sys_list = json.loads(systems_json) if systems_json else []
        except Exception:
            sys_list = []
        hist.append({
            "Attempt ID": aid,
            "日期(UTC)": attempt_date,
            "姓名": user_info["name"],
            "部门": user_info["department"],
            "员工号": user_info["employee_id"],
            "分数": sc,
            "是否通过(≥80)": "✅" if passed == 1 else "❌",
            "开始时间(UTC)": started_at,
            "提交时间(UTC)": submitted_at,
            "范围": ",".join(sys_list) if sys_list else "全部"
        })
    st.dataframe(pd.DataFrame(hist), use_container_width=True, hide_index=True)
else:
    st.write("暂无记录。")

# ---------------- 导出成绩单（Excel） ----------------
st.subheader("⬇️ 导出成绩单（Excel）")
def build_excel_for_user(user_key: str):
    attempts = quiz_db.list_attempts(user_key, limit=2000)
    attempt_rows = []
    for (aid, sc, passed, attempt_date, started_at, submitted_at, systems_json) in attempts:
        try:
            sys_list = json.loads(systems_json) if systems_json else []
        except Exception:
            sys_list = []
        attempt_rows.append({
            "Attempt ID": aid,
            "日期(UTC)": attempt_date,
            "姓名": user_info["name"],
            "部门": user_info["department"],
            "员工号": user_info["employee_id"],
            "分数": sc,
            "是否通过(≥80)": "PASS" if passed == 1 else "FAIL",
            "开始时间(UTC)": started_at,
            "提交时间(UTC)": submitted_at,
            "范围": ",".join(sys_list) if sys_list else "全部"
        })
    df_attempts = pd.DataFrame(attempt_rows)

    df_answers = pd.DataFrame()
    if attempts:
        latest_aid = attempts[0][0]
        ans = quiz_db.get_attempt_answers(latest_aid)
        df_answers = pd.DataFrame([{
            "Attempt ID": latest_aid,
            "题目ID": qid,
            "系统": sys,
            "题型": qtype,
            "你的答案": ua,
            "正确答案": ca,
            "是否正确": "✅" if ic == 1 else "❌"
        } for (qid, sys, qtype, ua, ca, ic) in ans])

    bio = BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        df_attempts.to_excel(writer, index=False, sheet_name="attempts")
        if not df_answers.empty:
            df_answers.to_excel(writer, index=False, sheet_name="latest_answers")
    bio.seek(0)
    return bio.getvalue()

excel_bytes = build_excel_for_user(user_key)
st.download_button(
    label="📥 下载我的成绩单（attempts + 最近一次明细）",
    data=excel_bytes,
    file_name=f"quiz_score_{user_key}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)

# ---------------- 中奖记录统计（最新200条） ----------------
st.subheader("📌 抽奖中奖记录（最新200条）")
wins = quiz_db.list_wins(limit=200)
if wins:
    st.dataframe(
        [{"中奖时间(UTC)": w[0], "姓名": w[1], "部门": w[2], "员工号": w[3], "奖项": w[4], "奖品": w[5], "AttemptID": w[6]} for w in wins],
        use_container_width=True,
        hide_index=True
    )
else:
    st.write("暂无中奖记录。")
