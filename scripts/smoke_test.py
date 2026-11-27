"""Offline smoke test: real MCP servers + Excel ledger, stubbed LLM (no API key needed)."""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hrdemo import ledger  # noqa: E402
from hrdemo.graph import GRAPH, Ctx  # noqa: E402
from hrdemo.mcp_client import MCPHub  # noqa: E402
from hrdemo.runner import stream_turn  # noqa: E402


def show(events):
    for e in events:
        if e["type"] == "status" and e["state"] != "running":
            print(f"   [{e['state']}] {e['step']} {e['detail']}")
        elif e["type"] in ("block", "fatal"):
            print(f"   <{e['type']}> " + e["text"][:200].replace("\n", " / "))


class FakeLLM:
    def __init__(self, intent, update=None):
        self.intent, self.update = intent, update

    async def json(self, system, user, history=None):
        if "意图识别" in system:
            return {"intent": self.intent, "reason": "stub"}
        if "资深招聘官" in system:
            return {"score": 40 + len(user) % 55, "reasons": "stub 理由"}
        if "招聘流程助手" in system:
            return self.update(json.loads(user))
        return {"action": "ask_user", "reason": "违反流程规则", "message_to_hr": "请 HR 确认是否回退"}

    async def stream(self, system, user, history=None):
        for t in ("汇报", "：", "OK"):
            yield t

    async def aclose(self):
        pass


async def turn(text, llm, shortlist):
    events, hub = [], MCPHub()
    await hub.connect()
    try:
        out = await GRAPH.ainvoke({"user_text": text, "history": [], "shortlist": shortlist},
                                  config={"configurable": {"ctx": Ctx(llm=llm, hub=hub, emit=events.append)}})
    finally:
        await hub.close()
    show(events)
    return out


def main():
    ledger.init_ledger(force=True)

    print("== 1. 未配置 Key 时的失败路径（真实 runner）")
    show(list(stream_turn("帮我拉取一下目前投递的简历", [], {})))

    print("== 2. 拉取 + AI 初筛")
    sl = asyncio.run(turn("帮我拉取一下目前投递的简历", FakeLLM("screen"), {}))["shortlist"]

    print("== 3. HR 决策：1、2 进一面，3 淘汰")
    asyncio.run(turn("1、2 进一面，3 淘汰", FakeLLM("update", lambda u: {"actions": [
        {"candidate_id": sl["1"]["candidate_id"], "target_stage": "待一面"},
        {"candidate_id": sl["2"]["candidate_id"], "target_stage": "待一面"},
        {"candidate_id": sl["3"]["candidate_id"], "target_stage": "已淘汰"}], "unresolved": []}), sl))

    print("== 4. 一面通过约二面 + 非法回退 + 无法识别")
    asyncio.run(turn("...", FakeLLM("update", lambda u: {"actions": [
        {"candidate_id": sl["1"]["candidate_id"], "target_stage": "待二面", "interview_time": "2026-12-01 15:00", "interviewer": "王工"},
        {"candidate_id": sl["2"]["candidate_id"], "target_stage": "待HR决定"}], "unresolved": ["某某：找不到"]}), sl))

    cands, logs = ledger.read_frames()
    print(cands[["姓名", "应聘岗位", "AI评分", "当前阶段", "面试时间", "面试官"]].to_string())
    print(logs.tail(4).to_string())
    ledger.init_ledger(force=True)


if __name__ == "__main__":
    main()
