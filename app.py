import json
import random
import hashlib
from datetime import datetime
from io import BytesIO

import pandas as pd
import streamlit as st

import quiz_db

st.set_page_config(page_title="随机答题", layout="wide")
st.title("🎯 随机答题（抽5题 / 自动评分 / ≥80分可转盘抽奖）")

# =====================
# 配置
# =====================
QUESTIONS_PER_QUIZ = 5
POINTS_PER_Q = 20
PASS_SCORE = 80

# ✅ 奖品总数（你已确认）
PRIZE_CONFIG = {
    "一等奖": {"total": 13, "prize_name": "一等奖"},
    "二等奖": {"total": 20, "prize_name": "二等奖"},
    "三等奖": {"total": 100, "prize_name": "三等奖"},
}
# 概率权重（可调：一等奖最低，三等奖最高）
PRIZE_WEIGHTS = {"一等奖": 1, "二等奖": 3, "三等奖": 10}

# ✅ 导出 token（自己改成复杂字符串，不要公开）
EXPORT_TOKEN = "CHANGE_ME_TO_A_LONG_RANDOM_STRING"

# =====================
# 初始化 DB + 奖品库存（只会初始化一次）
# =====================
quiz_db.init_db()
quiz_db.seed_prize_inventory_if_empty(PRIZE_CONFIG)

# =====================
# 导出模式（不在常规界面显示导出按钮）
# 访问：?export=1&token=你的token
# 会导出两张sheet：all_answer_records + prize_wins
# =====================
qp = st.query_params
export_flag = qp.get("export", ["0"])[0]
token = qp.get("token", [""])[0]

def _token_ok(t: str) -> bool:
    return hashlib.sha256(t.encode("utf-8")).hexdigest() == hashlib.sha256(EXPORT_TOKEN.encode("utf-8")).hexdigest()

if export_flag == "1":
    if not _token_ok(token):
        st.error("Unauthorized export.")
        st.stop()

    all_answers = quiz_db.export_all_answer_records()
    all_wins = quiz_db.export_all_prize_wins()

    df_answers = pd.DataFrame(all_answers)
    df_wins = pd.DataFrame(all_wins)

    bio = BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        df_answers.to_excel(writer, index=False, sheet_name="all_answer_records")
        df_wins.to_excel(writer, index=False, sheet_name="prize_wins")
    bio.seek(0)

    st.download_button(
        label="下载全量数据（答题明细+中奖记录）",
        data=bio.getvalue(),
        file_name="all_answer_records_and_prize_wins.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    st.stop()

# =====================
# 读取题库
# =====================
@st.cache_data
def load_bank(path="question_bank.json"):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

bank = load_bank()
all_questions = bank["questions"]

# =====================
# 考生信息
# =====================
st.subheader("👤 答题人信息（用于记录/抽奖核对）")
c1, c2, c3 = st.columns(3)
name = c1.text_input("姓名（必填）", value=st.session_state.get("name", ""))
dept = c2.text_input("部门（可选）", value=st.session_state.get("dept", ""))
empid = c3.text_input("员工编号（建议填写，用于唯一识别/一天一次）", value=st.session_state.get("empid", ""))

st.session_state["name"] = name
st.session_state["dept"] = dept
st.session_state["empid"] = empid

if not name.strip():
    st.warning("请先填写姓名再开始答题。")
    st.stop()

user_key = empid.strip() if empid.strip() else f"{name.strip()}|{dept.strip()}"
user_info = {"user_key": user_key, "name": name.strip(), "department": dept.strip(), "employee_id": empid.strip()}

today = quiz_db.today_utc()

# =====================
# 一天一次提交限制
# =====================
already = quiz_db.has_attempt_on_date(user_key, today)
latest_today = quiz_db.get_latest_attempt_on_date(user_key, today) if already else None
if already:
    st.info(f"你今天（{today} UTC）已提交过一次答题。今天不能再次提交。")

# =====================
# 题库范围筛选（可选）
# =====================
systems = sorted(list({q["system"] for q in all_questions}))
selected_systems = st.multiselect("选择题库范围（不选=全部）", systems, default=[])
candidate = [q for q in all_questions if (not selected_systems or q["system"] in selected_systems)]
st.caption(f"当前可用题目数：{len(candidate)}")
if len(candidate) < QUESTIONS_PER_QUIZ:
    st.error("题库题目少于5题，无法抽题。请调整筛选。")
    st.stop()

# =====================
# 避免重复抽题
# =====================
seen_set = quiz_db.get_seen_set(user_key)

def pick_questions_without_repeat(pool, seen, k):
