import json
import random
import streamlit as st

st.set_page_config(page_title="随机抽题考试", layout="wide")
st.title("🎯 随机抽题考试（抽5题 / 自动评分 / 80分可盖章）")

# ====== 读取题库 ======
@st.cache_data
def load_bank(path="question_bank.json"):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

bank = load_bank()
all_questions = bank["questions"]

# ====== 选择题库范围（可选：按系统筛选） ======
systems = sorted(list({q["system"] for q in all_questions}))
selected_systems = st.multiselect("选择题库范围（不选=全部）", systems, default=[])

candidate = [q for q in all_questions if (not selected_systems or q["system"] in selected_systems)]

st.caption(f"题库可用题目数量：{len(candidate)}")

# ====== 生成试卷：随机抽 5 题（保持一次考试固定） ======
if "quiz" not in st.session_state or st.button("🔄 重新抽题（新一套5题）"):
    if len(candidate) < 5:
        st.error("题库题目少于5题，无法抽题。请调整筛选或补充题库。")
        st.stop()
    st.session_state.quiz = random.sample(candidate, 5)
    st.session_state.submitted = False

quiz = st.session_state.quiz

# ====== 答题界面 ======
st.subheader("📝 答题区")
user_answers = []

for i, q in enumerate(quiz, start=1):
    st.markdown(f"### 第 {i} 题（{q['system']}）")

    if q["type"] == "single_choice":
        # options: {"A": "...", "B": "..."} → 展示为 "A. xxx"
        labels = [f"{k}. {v}" for k, v in q["options"].items()]
        choice = st.radio(q["question"], labels, key=f"q{i}")
        user_answers.append(choice.split(".", 1)[0])  # 取 A/B/C/D

    elif q["type"] == "true_false":
        tf_labels = ["√（正确）", "×（错误）"]
        choice = st.radio(q["question"], tf_labels, key=f"q{i}")
        user_answers.append(True if choice.startswith("√") else False)

    else:
        st.warning(f"未知题型：{q['type']}")
        user_answers.append(None)

# ====== 提交评分 ======
points_per_q = bank["meta"]["quiz_default"]["points_per_question"]
pass_score = bank["meta"]["quiz_default"]["pass_score"]

if st.button("✅ 提交并评分"):
    score = 0
    details = []

    for idx, q in enumerate(quiz):
        correct = q["answer"]
        ua = user_answers[idx]
        is_right = (ua == correct)

        if is_right:
            score += points_per_q

        details.append({
            "题号": idx + 1,
            "系统": q["system"],
            "题型": q["type"],
            "是否正确": "✅" if is_right else "❌",
            "你的答案": ua,
            "正确答案": correct
        })

    st.session_state.submitted = True
    st.session_state.last_score = score
    st.session_state.last_details = details

# ====== 展示结果 ======
if st.session_state.get("submitted"):
    score = st.session_state.last_score
    st.success(f"🎯 本次得分：{score} / 100")

    if score >= pass_score:
        st.success("✅ 恭喜通过（≥80分），可以参加线下盖章！")
    else:
        st.error("❌ 未通过（<80分），请复习后重新抽题再考。")

    st.markdown("### 详细结果")
    st.dataframe(st.session_state.last_details, use_container_width=True, hide_index=True)