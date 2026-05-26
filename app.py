import json
import random
from datetime import datetime
import streamlit as st

import quiz_db

st.set_page_config(page_title="随机抽题考试", layout="wide")
st.title("🎯 随机抽题考试（抽5题 / 自动评分 / ≥80分可线下盖章）")

# 初始化DB（本地SQLite：练手非常适合；但Cloud不保证长期持久化）【1-4f082d】
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

# ====== 用户信息（用于“谁考了”记录） ======
st.subheader("👤 考生信息")
col1, col2, col3 = st.columns(3)
name = col1.text_input("姓名（必填）", value=st.session_state.get("name", ""))
dept = col2.text_input("部门（可选）", value=st.session_state.get("dept", ""))
empid = col3.text_input("员工编号（建议填写，用于唯一识别）", value=st.session_state.get("empid", ""))

st.session_state["name"] = name
st.session_state["dept"] = dept
st.session_state["empid"] = empid

if not name.strip():
    st.warning("请先填写姓名再开始考试。")
    st.stop()

# user_key：优先员工号；没有则用 姓名|部门
user_key = empid.strip() if empid.strip() else f"{name.strip()}|{dept.strip()}"
user_info = {"user_key": user_key, "name": name.strip(), "department": dept.strip(), "employee_id": empid.strip()}

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
    """
    优先从未做过题里抽；不够k则用已做过的补齐（并提示）
    """
    unseen = [q for q in pool if q["id"] not in seen]
    if len(unseen) >= k:
        return random.sample(unseen, k), 0
    # 不足：全部未做过 + 从已做过补齐
    picked = unseen[:]
    need = k - len(picked)
    seen_pool = [q for q in pool if q["id"] in seen]
    picked += random.sample(seen_pool, need)
    return picked, need

# ====== 生成试卷（一次会话固定） ======
if "quiz" not in st.session_state:
    st.session_state.started_at = datetime.utcnow().isoformat(timespec="seconds")
    st.session_state.quiz, st.session_state.repeat_fill_count = pick_questions_without_repeat(candidate, seen_set, QUESTIONS_PER_QUIZ)
    st.session_state.submitted = False

