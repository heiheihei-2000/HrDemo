import altair as alt
import pandas as pd
import streamlit as st

from hrdemo import config, ledger
from hrdemo.runner import stream_turn

st.set_page_config(page_title="AI 招聘助手 Demo", layout="wide")
ledger.init_ledger()

ss = st.session_state
ss.setdefault("messages", [])
ss.setdefault("shortlist", {})
ss.setdefault("active_shortlist", None)

STATE_STYLE = {
    "done": ":green[:material/check_circle:]", "error": ":red[:material/error:]",
    "decision": ":orange[:material/psychology:] LLM 决策", "skipped": ":gray[:material/skip_next:] 已跳过",
    "aborted": ":red[:material/block:] 未完成",
}
DECISION_TO_STAGE = {"进一面": "待一面", "淘汰": "已淘汰"}
LEDGER_VIEW_COLS = ["候选人ID", "姓名", "应聘岗位", "学历", "工作年限", "期望薪资", "AI评分", "AI建议",
                    "当前阶段", "面试时间", "面试官", "更新时间"]


def render_ledger(ph) -> None:
    try:
        cands, logs = ledger.read_frames()
    except Exception as e:  # noqa: BLE001
        ph.warning(f"台账读取失败：{e}")
        return
    with ph.container():
        stage = cands["当前阶段"] if not cands.empty else pd.Series(dtype=str)
        m = st.columns(5)
        m[0].metric("候选人", len(cands))
        m[1].metric("待HR决定", int((stage == "待HR决定").sum()))
        m[2].metric("面试中", int(stage.isin(list(config.INTERVIEW_STAGES)).sum()))
        m[3].metric("Offer", int(stage.isin(["待发Offer", "已发Offer"]).sum()))
        m[4].metric("已淘汰/放弃", int(stage.isin(["已淘汰", "已放弃"]).sum()))
        if cands.empty:
            st.info("台账暂无数据。试试对助手说：帮我拉取一下目前投递的简历")
            return
        counts = stage.value_counts().reindex(config.STAGES, fill_value=0).rename_axis("阶段").reset_index(name="人数")
        st.altair_chart(
            alt.Chart(counts).mark_bar().encode(x=alt.X("阶段:N", sort=config.STAGES, axis=alt.Axis(labelAngle=0)),
                                                y=alt.Y("人数:Q", axis=alt.Axis(tickMinStep=1)), tooltip=["阶段", "人数"])
            .properties(height=200),
            width="stretch",
        )
        st.dataframe(cands[LEDGER_VIEW_COLS], hide_index=True)
        st.caption(f"最近跟进流水（{config.LEDGER_PATH.name}）")
        st.dataframe(logs.tail(8).iloc[::-1], hide_index=True)


def history_for_llm() -> list[dict]:
    return [{"role": m["role"], "content": m["content"][:800]} for m in ss.messages[-6:]]


def step_line(ev: dict) -> str:
    return f"{STATE_STYLE[ev['state']]} {ev['step']}" + (f" — {ev['detail']}" if ev["detail"] else "")


def phase_label(label: str, state: str) -> str:
    return f":shimmer[{label}]" if state == "running" else label


def render_phases(phases: list[dict]) -> None:
    for p in phases:
        with st.status(p["label"], type="step", state=p["state"], expanded=False):
            for line in p["lines"] or ["（无明细）"]:
                st.markdown(line)


def shortlist_form(idx: int, rows: list[dict]) -> list[dict] | None:
    df = pd.DataFrame(rows)
    df.insert(0, "决定", "待定")
    with st.form(f"decide_{idx}", border=False):
        edited = st.data_editor(
            df, hide_index=True, key=f"editor_{idx}", alt="AI 初筛结果，可在决定列中选择",
            disabled=[c for c in df.columns if c != "决定"],
            column_config={
                "决定": st.column_config.SelectboxColumn("决定", options=["待定", *DECISION_TO_STAGE], required=True),
                "理由": st.column_config.TextColumn("理由", width="large"),
            },
        )
        submitted = st.form_submit_button("提交决定", type="primary", icon=":material/check:")
    if not submitted:
        return None
    picks = edited[edited["决定"] != "待定"]
    if picks.empty:
        st.warning("请至少为一位候选人选择「进一面」或「淘汰」。")
        return None
    return [{"candidate_id": ss.shortlist[str(r["编号"])]["candidate_id"], "name": r["姓名"],
             "target_stage": DECISION_TO_STAGE[r["决定"]], "note": "HR 在初筛表中决定"}
            for _, r in picks.iterrows() if str(r["编号"]) in ss.shortlist]


with st.sidebar:
    st.header("AI 招聘助手 Demo")
    if not config.DEEPSEEK_API_KEY:
        st.warning("未检测到 DEEPSEEK_API_KEY，请在 .env 中填写后重启应用。")
    if not list(config.MOCK_DIR.glob("boss_export_*.xlsx")):
        st.error("未找到模拟数据，请先运行：python scripts/make_mock_data.py")
    st.markdown(
        "**试试这样说：**\n"
        "- 帮我拉取一下目前投递的简历\n"
        "- 1、2、5 进一面，3 淘汰\n"
        "- 张伟一面通过，下周二下午3点二面，面试官王工\n"
        "- 现在招聘进度怎么样？"
    )
    if st.button("重置演示数据", type="secondary"):
        ledger.init_ledger(force=True)
        ss.messages, ss.shortlist, ss.active_shortlist = [], {}, None
        st.rerun()

left, right = st.columns([5, 6], gap="large")
with right:
    st.subheader("实时招聘台账")
    ledger_ph = st.empty()
    render_ledger(ledger_ph)

submitted_decisions = None
with left:
    st.subheader("对话")
    for i, msg in enumerate(ss.messages):
        with st.chat_message(msg["role"]):
            render_phases(msg.get("phases", []))
            for b in msg.get("blocks", []):
                st.markdown(b)
            if rows := msg.get("shortlist_rows"):
                if i == ss.active_shortlist:
                    submitted_decisions = shortlist_form(i, rows) or submitted_decisions
                else:
                    st.dataframe(pd.DataFrame(rows), hide_index=True)
            if msg["content"]:
                st.markdown(msg["content"])

typed = st.chat_input("对招聘助手说点什么…", submit_mode="disable")
prompt, decisions = typed, None
if submitted_decisions:
    decisions = submitted_decisions
    groups = {}
    for d in decisions:
        groups.setdefault(d["target_stage"], []).append(d["name"])
    prompt = "提交决定：" + "；".join(f"{'进一面' if s == '待一面' else '淘汰'} {'、'.join(n)}" for s, n in groups.items())

if prompt:
    history = history_for_llm()
    with left:
        with st.chat_message("user"):
            st.markdown(prompt)
        with st.chat_message("assistant"):
            timeline, answer = st.container(), st.container()
            with answer:
                blocks_ph, table_ph, text_ph = st.container(), st.empty(), st.empty()
            phases: dict[str, dict] = {}
            current: str | None = None
            blocks, text, rows = [], "", None

            def set_phase(pid: str, label: str, state: str) -> None:
                global current
                if pid not in phases:
                    with timeline:
                        box = st.status(phase_label(label, state), type="step", state=state, expanded=True)
                        with box:
                            lines_box, cur = st.container(), st.empty()
                    phases[pid] = {"data": {"label": label, "state": state, "lines": []},
                                   "box": box, "lines": lines_box, "cur": cur}
                    current = pid
                p = phases[pid]
                p["data"].update(label=label, state=state)
                p["box"].update(label=phase_label(label, state), state=state, expanded=state != "complete")
                if state != "running":
                    p["cur"].empty()

            for ev in stream_turn(prompt, history, ss.shortlist, decisions):
                t = ev["type"]
                if t == "phase":
                    set_phase(ev["id"], ev["label"], ev["state"])
                elif t == "status":
                    if current is None:
                        set_phase("prep", "准备工具服务", "running")
                    p = phases[current]
                    if ev["state"] == "running":
                        p["cur"].markdown(f":shimmer[{ev['step']}…]")
                        continue
                    line = step_line(ev)
                    p["data"]["lines"].append(line)
                    p["lines"].markdown(line)
                    p["cur"].empty()
                    if ev["state"] == "aborted":
                        set_phase(current, p["data"]["label"].rstrip("…") + "（未完成）", "error")
                elif t == "shortlist":
                    rows = ev["rows"]
                    table_ph.dataframe(pd.DataFrame(rows), hide_index=True)
                elif t == "block":
                    blocks.append(ev["text"])
                    blocks_ph.markdown(ev["text"])
                elif t == "reset":
                    text = ""
                    text_ph.empty()
                elif t == "token":
                    text += ev["text"]
                    text_ph.markdown(text + "▌")
                elif t == "ledger_changed":
                    render_ledger(ledger_ph)
                elif t == "final":
                    ss.shortlist = ev["shortlist"] or {}
                elif t == "fatal":
                    blocks.append(f"**系统异常**：{ev['text']}")
                    blocks_ph.error(ev["text"])
            text_ph.markdown(text)
            for pid, p in phases.items():
                if p["data"]["state"] == "running":
                    set_phase(pid, p["data"]["label"].rstrip("…") + "（未完成）", "error")

    ss.messages.append({"role": "user", "content": prompt})
    ss.messages.append({"role": "assistant", "content": text, "blocks": blocks, "shortlist_rows": rows,
                        "phases": [p["data"] for p in phases.values()]})
    if rows:
        ss.active_shortlist = len(ss.messages) - 1
    elif decisions:
        ss.active_shortlist = None
    st.rerun()
