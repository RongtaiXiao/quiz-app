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

# =========================
# 基本配置
# =========================
QUESTIONS_PER_QUIZ = 5
POINTS_PER_Q = 20
PASS_SCORE = 80

# ✅ 奖品库存（你已确认）
PRIZE_CONFIG = {
    "一等奖": {"total": 13, "prize_name": "一等奖"},
    "二等奖": {"total": 20, "prize_name": "二等奖"},
    "三等奖": {"total": 100, "prize_name": "三等奖"},
}
# 概率权重（可调：一等奖最低，三等奖最高）
PRIZE_WEIGHTS = {"一等奖": 1, "二等奖": 3, "三等奖": 10}

# ✅ 隐藏链接 Token（请改成复杂字符串）
EXPORT_TOKEN = "CHANGE_ME_TO_A_LONG_RANDOM_STRING"

# =========================
# 初始化DB + 初始化奖品库存（仅第一次初始化）
# =========================
quiz_db.init_db()
quiz_db.seed_prize_inventory_if_empty(PRIZE_CONFIG)

# =========================
# Query Params 工具函数
# =========================
qp = st.query_params

def qget(key, default=""):
    v = qp.get(key, None)
    if v is None:
        return default
    if isinstance(v, list):
        return v[0] if v else default
    return v

def _token_ok(t: str) -> bool:
    return hashlib.sha256(t.encode("utf-8")).hexdigest() == hashlib.sha256(EXPORT_TOKEN.encode("utf-8")).hexdigest()

export_flag = qget("export", "0")
admin_action = qget("admin", "")
confirm = qget("confirm", "")
token = qget("token", "")

# =========================
# 1) 隐藏导出链接：?export=1&token=xxx
# =========================
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

# =========================
# 2) 管理员隐藏链接
#   - 查看库存：?admin=view_stock&token=xxx
#   - 重置库存：?admin=reset_stock&confirm=YES&token=xxx
# =========================
if admin_action:
    if not _token_ok(token):
        st.error("Unauthorized admin action.")
        st.stop()

    if admin_action == "view_stock":
        st.title("🔧 管理员：查看库存与中奖统计")

        inv = quiz_db.get_prize_inventory()
        st.subheader("🎁 奖品库存（剩余/总数）")
        st.table([{"奖项": t, "剩余": r, "总数": tot} for (t, tot, r) in inv])

        summary = quiz_db.get_win_summary()
        st.subheader("📊 已中奖数量统计")
        st.table([
            {"奖项": "一等奖", "已发放": summary["一等奖"]},
            {"奖项": "二等奖", "已发放": summary["二等奖"]},
            {"奖项": "三等奖", "已发放": summary["三等奖"]},
        ])

        st.subheader("🧾 最近中奖记录（最新200条）")
        wins = quiz_db.list_wins(limit=200)
        if wins:
            st.dataframe(
                [{"中奖时间(UTC)": w[0], "姓名": w[1], "部门": w[2], "员工号": w[3], "奖项": w[4], "奖品": w[5], "AttemptID": w[6]} for w in wins],
                use_container_width=True,
                hide_index=True
            )
        else:
            st.write("暂无中奖记录。")

        st.stop()

    if admin_action == "reset_stock":
        if confirm != "YES":
            st.error("需要确认参数：confirm=YES 才会执行重置。")
            st.info("正确用法：?admin=reset_stock&confirm=YES&token=xxx")
            st.stop()

        quiz_db.reset_prize_inventory_and_wins(PRIZE_CONFIG)

        st.success("✅ 已完成库存重置：一等奖13 / 二等奖20 / 三等奖100")
        st.warning("⚠️ 已清空所有中奖记录（prize_wins）。请确保这是活动重置操作。")
        st.stop()

    st.error("未知管理员操作。支持：admin=view_stock 或 admin=reset_stock")
    st.stop()

# =========================
# 常规页面：答题功能
# =========================
@st.cache_data
def load_bank(path="question_bank.json"):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

bank = load_bank()
all_questions = bank["questions"]

# -------- 答题人信息 --------
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

already = quiz_db.has_attempt_on_date(user_key, today)
if already:
    st.info(f"你今天（{today} UTC）已提交过一次答题，今天不能再次提交。")

# -------- 题库范围筛选 --------
systems = sorted(list({q["system"] for q in all_questions}))
selected_systems = st.multiselect("选择题库范围（不选=全部）", systems, default=[])
candidate = [q for q in all_questions if (not selected_systems or q["system"] in selected_systems)]
st.caption(f"当前可用题目数：{len(candidate)}")
if len(candidate) < QUESTIONS_PER_QUIZ:
    st.error("题库题目少于5题，无法抽题。请调整筛选。")
    st.stop()

# -------- 避免重复抽题 --------
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

# -------- 生成5题（仅今日未提交）--------
if "quiz_for" not in st.session_state or st.session_state.get("quiz_for") != user_key:
    st.session_state.quiz_for = user_key
    st.session_state.submitted = False
    st.session_state.last_attempt_id = None

if not already and ("quiz" not in st.session_state or st.session_state.get("quiz") is None):
    st.session_state.started_at = datetime.utcnow().isoformat(timespec="seconds")
    st.session_state.quiz, st.session_state.repeat_fill_count = pick_questions_without_repeat(candidate, seen_set, QUESTIONS_PER_QUIZ)

# -------- 答题区（仅今日未提交显示）--------
if not already:
    quiz = st.session_state.quiz
    repeat_fill = st.session_state.get("repeat_fill_count", 0)
    if repeat_fill > 0:
        st.info(f"你未做过的题不足5题，本次有 {repeat_fill} 题从已做过题中补齐。")

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

    if st.button("✅ 提交答题并评分"):
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

        # 今日已提交后，不再显示新的题
        st.session_state.quiz = None
        st.rerun()

# -------- 我的答题记录（不限制10次）--------
st.subheader("🧾 我的答题记录")
rows = quiz_db.list_attempts(user_key, limit=2000)

if not rows:
    st.write("暂无答题记录。")
else:
    for (aid, sc, passed, attempt_date, started_at, submitted_at, systems_json) in rows:
        st.markdown(f"### Attempt ID: {aid} | 得分：{sc}/100 | {'✅通过' if passed==1 else '❌未通过'} | 日期(UTC)：{attempt_date}")

        ans = quiz_db.get_attempt_answers(int(aid))
        df = pd.DataFrame([{
            "题目ID": qid,
            "系统": sys,
            "题型": qtype,
            "你的答案": ua,
            "正确答案": ca,
            "是否正确": "✅" if ic == 1 else "❌"
        } for (qid, sys, qtype, ua, ca, ic) in ans])
        st.dataframe(df, use_container_width=True, hide_index=True)

        # 通过才显示抽奖（一次 attempt 只抽一次）
        if passed == 1:
            st.markdown("#### 🎡 转盘抽奖（限量）")

            inv = quiz_db.get_prize_inventory()
            st.table([{"奖项": t, "剩余": r, "总数": tot} for (t, tot, r) in inv])

            existing = quiz_db.get_win_by_attempt(int(aid))
            if existing:
                tier, prize_name, win_time = existing
                st.info(f"抽奖结果：{tier}（{prize_name}） | 时间：{win_time} UTC")
            else:
                if st.button(f"开始抽奖（Attempt {aid}）", key=f"draw_{aid}"):
                    result = quiz_db.draw_prize_once(
                        attempt_id=int(aid),
                        user_info=user_info,
                        prize_config=PRIZE_CONFIG,
                        weights=PRIZE_WEIGHTS
                    )
                    if result["tier"] == "未中奖":
                        st.warning("🍀 未中奖（可能奖品已发完或概率未中）")
                    else:
                        st.success(f"🎉 恭喜中奖：{result['tier']}（{result['prize_name']}）")
                    st.caption("请截图保存用于兑奖核对。")
                    st.rerun()