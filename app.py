import json
import random
from datetime import datetime
from io import BytesIO

import pandas as pd
import streamlit as st

import quiz_db

st.set_page_config(page_title="随机抽题考试", layout="wide")
st.title("🎯 随机抽题考试（抽5题 / 自动评分 / ≥80分可线下盖章）")

# 初始化 DB（提醒：Community Cloud 本地文件不保证持久化）
quiz_db.init_db()

# ====== 读取题库 ======
@st.cache_data
def load_bank(path="question_bank.json"):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

bank = load_bank()
all_questions = bank["questions"]

POINTS_PER_Q = bank["meta"]["quiz_default"]["points_per_question"]
PASS_SCORE = bank["meta"]["quiz_default"]["pass_score"]
QUESTIONS_PER_QUIZ = bank["meta"]["quiz_default"]["questions_per_quiz"]

# ====== 用户信息（用于“谁考了”记录）=====
st.subheader("👤 考生信息（用于成绩记录/盖章核对）")
col1, col2, col3 = st.columns(3)
name = col1.text_input("姓名（必填）", value=st.session_state.get("name", ""))
dept = col2.text_input("部门（可选）", value=st.session_state.get("dept", ""))
empid = col3.text_input("员工编号（强烈建议填写，用于锁定“一天一次提交”）", value=st.session_state.get("empid", ""))

st.session_state["name"] = name
st.session_state["dept"] = dept
st.session_state["empid"] = empid

if not name.strip():
    st.warning("请先填写姓名再开始考试。")
    st.stop()

# user_key：优先员工号；没有则姓名|部门（没员工号时防刷能力会弱一些）
user_key = empid.strip() if empid.strip() else f"{name.strip()}|{dept.strip()}"
user_info = {"user_key": user_key, "name": name.strip(), "department": dept.strip(), "employee_id": empid.strip()}

today = quiz_db.today_utc()

# ====== 今天是否已经提交过？（一天一次提交限制）=====
already = quiz_db.has_attempt_on_date(user_key, today)
latest_today = quiz_db.get_latest_attempt_on_date(user_key, today) if already else None

if already:
    st.warning(f"⛔ 你今天（{today}，UTC日期）已经提交过一次考试，为防刷分，今天不能再次提交。")
    if latest_today:
        attempt_id, score, passed, submitted_at, systems_json = latest_today
        st.info(f"你今天最新一次：Attempt ID={attempt_id}，得分={score}，是否通过={'✅' if passed==1 else '❌'}，提交时间={submitted_at}")

# ====== 选择题库范围（系统筛选） ======
systems = sorted(list({q["system"] for q in all_questions}))
selected_systems = st.multiselect("选择题库范围（不选=全部）", systems, default=[])

candidate = [q for q in all_questions if (not selected_systems or q["system"] in selected_systems)]
st.caption(f"当前可用题目数：{len(candidate)}（系统筛选后）")

if len(candidate) < QUESTIONS_PER_QUIZ:
    st.error("题目数量不足5题，请调整筛选范围。")
    st.stop()

# ====== 避免重复抽题：读取用户已做过的题ID ======
seen_set = quiz_db.get_seen_set(user_key)

def pick_questions_without_repeat(pool, seen, k):
    """优先抽未做过的；不够k则用已做过补齐，并返回补齐数量"""
    unseen = [q for q in pool if q["id"] not in seen]
    if len(unseen) >= k:
        return random.sample(unseen, k), 0
    picked = unseen[:]
    need = k - len(picked)
    seen_pool = [q for q in pool if q["id"] in seen]
    picked += random.sample(seen_pool, need)
    return picked, need

# ====== 生成试卷（一次会话固定） ======
if "quiz" not in st.session_state:
    st.session_state.started_at = datetime.utcnow().isoformat(timespec="seconds")
    st.session_state.quiz, st.session_state.repeat_fill_count = pick_questions_without_repeat(
        candidate, seen_set, QUESTIONS_PER_QUIZ
    )
    st.session_state.submitted = False

colA, colB = st.columns([1,1])
if colA.button("🔄 重新抽题（新一套5题）"):
    st.session_state.started_at = datetime.utcnow().isoformat(timespec="seconds")
    st.session_state.quiz, st.session_state.repeat_fill_count = pick_questions_without_repeat(
        candidate, seen_set, QUESTIONS_PER_QUIZ
    )
    st.session_state.submitted = False
    st.rerun()

if colB.button("♻️ 清空我的做题历史（重新开始无重复抽题）"):
    quiz_db.reset_seen(user_key)
    st.success("已清空你的历史做题记录。请点击“重新抽题”。")
    st.rerun()

quiz = st.session_state.quiz
repeat_fill = st.session_state.get("repeat_fill_count", 0)
if repeat_fill > 0:
    st.info(f"题库中你未做过的题不足5题，本次有 {repeat_fill} 题从已做过题中补齐。")

# ====== 答题界面 ======
st.subheader("📝 答题区（共5题）")
user_answers = []

for i, q in enumerate(quiz, start=1):
    st.markdown(f"### 第 {i} 题（{q['system']}）")

    if q["type"] == "single_choice":
        labels = [f"{k}. {v}" for k, v in q["options"].items()]
        choice = st.radio(q["question"], labels, key=f"q{i}")
        user_answers.append(choice.split(".", 1)[0])  # A/B/C/D

    elif q["type"] == "true_false":
        tf_labels = ["√（正确）", "×（错误）"]
        choice = st.radio(q["question"], tf_labels, key=f"q{i}")
        user_answers.append(True if choice.startswith("√") else False)

    else:
        st.warning(f"未知题型：{q['type']}")
        user_answers.append(None)

# ====== 提交并评分（如果今天已提交过，则禁用提交按钮）=====
submit_disabled = already

if st.button("✅ 提交并评分", disabled=submit_disabled):
    started_at = st.session_state.get("started_at", datetime.utcnow().isoformat(timespec="seconds"))
    submitted_at = datetime.utcnow().isoformat(timespec="seconds")
    attempt_date = today  # 以UTC日期控制“一天一次”

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

    # 1) 保存考试记录
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

    # 2) 标记已做题（用于“避免重复抽题”）
    quiz_db.mark_seen(user_key, question_ids)

    st.session_state.submitted = True
    st.session_state.last_score = score
    st.session_state.last_attempt_id = attempt_id
    st.session_state.last_details = answer_rows

    st.rerun()

# ====== 展示结果 ======
if st.session_state.get("submitted"):
    score = st.session_state.last_score
    attempt_id = st.session_state.last_attempt_id

    st.success(f"🎯 本次得分：{score} / 100（Attempt ID: {attempt_id}）")

    if score >= PASS_SCORE:
        st.success("✅ 恭喜通过（≥80分），可以参加线下盖章！")
    else:
        st.error("❌ 未通过（<80分），请复习后改天再考。")

    st.markdown("### 详细答题结果")
    st.dataframe(st.session_state.last_details, use_container_width=True, hide_index=True)

# ====== 我的考试历史（最近10次） ======
st.subheader("📚 我的考试记录（最近10次）")
rows = quiz_db.list_attempts(user_key, limit=10)
if not rows:
    st.write("暂无记录。")
else:
    hist = []
    for (attempt_id, score, passed, attempt_date, started_at, submitted_at, systems_json) in rows:
        try:
            sys_list = json.loads(systems_json) if systems_json else []
        except Exception:
            sys_list = []
        hist.append({
            "Attempt ID": attempt_id,
            "日期(UTC)": attempt_date,
            "姓名": user_info["name"],
            "部门": user_info["department"],
            "员工号": user_info["employee_id"],
            "分数": score,
            "是否通过(≥80)": "✅" if passed == 1 else "❌",
            "开始时间(UTC)": started_at,
            "提交时间(UTC)": submitted_at,
            "范围": ",".join(sys_list) if sys_list else "全部"
        })
    df_hist = pd.DataFrame(hist)
    st.dataframe(df_hist, use_container_width=True, hide_index=True)

# ====== 导出Excel成绩单（你要的功能）=====
st.subheader("⬇️ 导出成绩单（Excel）")

def build_excel_for_user(user_key: str):
    attempts = quiz_db.list_attempts(user_key, limit=2000)

    # Sheet1: attempts
    attempt_rows = []
    for (attempt_id, score, passed, attempt_date, started_at, submitted_at, systems_json) in attempts:
        try:
            sys_list = json.loads(systems_json) if systems_json else []
        except Exception:
            sys_list = []
        attempt_rows.append({
            "Attempt ID": attempt_id,
            "日期(UTC)": attempt_date,
            "姓名": user_info["name"],
            "部门": user_info["department"],
            "员工号": user_info["employee_id"],
            "分数": score,
            "是否通过(≥80)": "PASS" if passed == 1 else "FAIL",
            "开始时间(UTC)": started_at,
            "提交时间(UTC)": submitted_at,
            "范围": ",".join(sys_list) if sys_list else "全部"
        })
    df_attempts = pd.DataFrame(attempt_rows)

    # Sheet2: answers（默认导出最近一次attempt的明细，便于盖章核对）
    df_answers = pd.DataFrame()
    if attempts:
        latest_attempt_id = attempts[0][0]
        ans = quiz_db.get_attempt_answers(latest_attempt_id)
        df_answers = pd.DataFrame([{
            "Attempt ID": latest_attempt_id,
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

st.caption("提示：成绩单字段包含姓名/部门/员工号/得分/是否通过/时间，便于线下盖章核对。【2-a582d9】")