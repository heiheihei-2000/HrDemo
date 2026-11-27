import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from .config import INTERVIEW_STAGES, STAGES, TERMINAL_STAGES
from .llm import LLM, LLMNotConfigured
from .mcp_client import MCPHub

Emit = Callable[[dict], None]

BOSS_TO_LEDGER = {
    "投递编号": "候选人ID", "姓名": "姓名", "性别": "性别", "年龄": "年龄", "学历": "学历", "毕业院校": "院校",
    "工作年限": "工作年限", "当前公司": "当前公司", "投递职位": "应聘岗位", "期望薪资": "期望薪资",
    "联系电话": "手机号", "投递时间": "投递时间",
}
INTENT_LABEL = {"screen": "拉取并筛选简历", "update": "更新候选人状态", "query": "查询进度", "chat": "其他对话"}
ACTION_LABEL = {"retry": "重试", "skip": "跳过", "ask_user": "请 HR 确认", "abort": "终止"}
WEEKDAYS = "一二三四五六日"


@dataclass
class Ctx:
    llm: LLM
    hub: MCPHub
    emit: Emit


class TurnState(TypedDict, total=False):
    user_text: str
    history: list[dict]
    shortlist: dict
    shortlist_rows: list[dict]
    decisions: list[dict]
    intent: str


class StepAborted(Exception):
    def __init__(self, step: str, reason: str, decision: dict):
        super().__init__(f"{step}: {reason}")
        self.step, self.reason, self.decision = step, reason, decision

    def as_markdown(self) -> str:
        d = self.decision
        lines = [f"**未完成：{self.step}**", "", f"- 原因：{self.reason}",
                 f"- 处置决策：{ACTION_LABEL.get(d['action'], d['action'])}（{d['reason']}）"]
        if d.get("message_to_hr"):
            lines.append(f"- {d['message_to_hr']}")
        return "\n".join(lines)


SKIPPED = object()


def today() -> str:
    d = datetime.now()
    return f"{d:%Y-%m-%d %H:%M} 星期{WEEKDAYS[d.weekday()]}"


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


# ---------------------------------------------------------------- step runner + LLM recovery

RECOVERY_SYSTEM = """你是招聘助手的流程调度员。某个处理步骤失败了，请根据步骤名称、错误原因和已尝试次数决定下一步，输出 JSON：
{"action": "retry|skip|ask_user|abort", "reason": "不超过30字的中文理由", "message_to_hr": "给HR的一句话说明与建议（ask_user/abort 时必填）"}
决策规则：
- retry：网络超时、限流、连接中断、文件被占用等临时性问题（已尝试3次及以上不要再 retry）
- skip：只影响单个候选人/单条记录，跳过后其他记录仍可继续处理
- ask_user：HR 指令有歧义、违反流程规则（如阶段回退、终态变更）、需要 HR 补充信息或确认
- abort：配置缺失、数据文件缺失等系统性故障，继续执行没有意义"""


async def decide_recovery(ctx: Ctx, step: str, error: Exception, attempt: int) -> dict:
    reason = str(error)
    if isinstance(error, LLMNotConfigured):
        return {"action": "abort", "reason": "大模型未配置，无法继续", "message_to_hr": reason}
    try:
        d = await ctx.llm.json(RECOVERY_SYSTEM, dumps({"步骤": step, "错误类型": type(error).__name__,
                                                       "错误原因": reason, "已尝试次数": attempt}))
        if d.get("action") not in ACTION_LABEL:
            raise ValueError(f"非法决策 {d.get('action')}")
        return d
    except Exception as e:
        why = "大模型未配置" if isinstance(e, LLMNotConfigured) else type(e).__name__
        return {"action": "abort", "reason": f"无法自动决策（{why}），已终止",
                "message_to_hr": f"步骤「{step}」失败：{reason}"}


async def run_step(ctx: Ctx, name: str, fn: Callable[[], Awaitable[Any]],
                   summarize: Callable[[Any], str] = lambda r: "", max_retries: int = 2, quiet: bool = False) -> Any:
    """quiet=True hides running/done lines; failures and LLM decisions are always shown."""
    attempt = 0
    while True:
        attempt += 1
        if not quiet:
            ctx.emit({"type": "status", "step": name, "state": "running", "detail": f"第 {attempt} 次尝试" if attempt > 1 else ""})
        try:
            result = await fn()
        except Exception as e:
            ctx.emit({"type": "status", "step": name, "state": "error", "detail": f"{type(e).__name__}: {e}"})
            decision = await decide_recovery(ctx, name, e, attempt)
            if decision["action"] == "retry" and attempt > max_retries:
                decision = {"action": "abort", "reason": f"已重试 {max_retries} 次仍失败",
                            "message_to_hr": decision.get("message_to_hr") or f"步骤「{name}」多次失败：{e}"}
            ctx.emit({"type": "status", "step": name, "state": "decision",
                      "detail": f"{ACTION_LABEL[decision['action']]}：{decision['reason']}"})
            if decision["action"] == "retry":
                await asyncio.sleep(1.5 * attempt)
                continue
            if decision["action"] == "skip":
                ctx.emit({"type": "status", "step": name, "state": "skipped", "detail": decision["reason"]})
                return SKIPPED
            ctx.emit({"type": "status", "step": name, "state": "aborted", "detail": decision["reason"]})
            raise StepAborted(name, str(e), decision) from e
        if not quiet:
            ctx.emit({"type": "status", "step": name, "state": "done", "detail": summarize(result)})
        return result


async def stream_reply(ctx: Ctx, name: str, system: str, user: str, history: list[dict] | None = None) -> Any:
    async def fn():
        ctx.emit({"type": "reset"})
        parts = []
        async for t in ctx.llm.stream(system, user, history):
            parts.append(t)
            ctx.emit({"type": "token", "text": t})
        return "".join(parts)

    return await run_step(ctx, name, fn, quiet=True)


def ledger_changed(ctx: Ctx) -> None:
    ctx.emit({"type": "ledger_changed"})


def phase(ctx: Ctx, pid: str, label: str, state: str = "running") -> None:
    """User-facing stage in the chat timeline; repeated pid updates the same stage."""
    ctx.emit({"type": "phase", "id": pid, "label": label, "state": state})


# ---------------------------------------------------------------- router

ROUTER_SYSTEM = """你是招聘助手的意图识别器。根据 HR 最新一条消息（可参考最近对话）判断意图，输出 JSON：
{"intent": "screen|update|query|chat", "reason": "不超过20字"}
- screen：拉取/查看新投递的简历、做简历初筛、推荐候选人
- update：对候选人做决定或同步面试进度，如“1、3进一面”“张伟淘汰”“李娜一面通过，周四下午二面”“发offer”“已入职”，包括只回复编号
- query：询问招聘进度、统计数据、某位候选人的情况
- chat：其他闲聊或无法归类"""


async def router_node(state: TurnState, ctx: Ctx) -> dict:
    if state.get("decisions"):
        phase(ctx, "intent", f"收到 HR 在表格中提交的 {len(state['decisions'])} 项决定", "complete")
        return {"intent": "update"}
    phase(ctx, "intent", "正在理解你的需求…")
    user = dumps({"当前时间": today(), "是否有待决策名单": bool(state.get("shortlist")), "HR消息": state["user_text"]})
    d = await run_step(ctx, "意图识别", lambda: ctx.llm.json(ROUTER_SYSTEM, user, state.get("history")),
                       summarize=lambda d: f"{INTENT_LABEL.get(d.get('intent'), '其他对话')}（{d.get('reason', '')}）")
    intent = d.get("intent") if d is not SKIPPED else "chat"
    intent = intent if intent in INTENT_LABEL else "chat"
    phase(ctx, "intent", f"需求：{INTENT_LABEL[intent]}", "complete")
    return {"intent": intent}


# ---------------------------------------------------------------- screening sub-agent

SCORE_SYSTEM = """你是资深招聘官。根据岗位要求评估候选人简历，输出 JSON：
{"score": 0到100的整数, "reasons": "不超过60字的中文理由，说明主要匹配点与风险"}
评分参考：硬性要求（年限、学历、核心技能）不满足要明显扣分；期望薪资超出岗位范围需指出；75分以上为推荐，60-74待定，60以下不推荐。"""

SCREEN_SUMMARY_SYSTEM = """你是招聘助手。HR 已经看到下方 AI 初筛结果表（带编号）。请用 3-5 句话简洁总结：
各岗位推荐几人、最值得优先约面的是谁及原因、需要注意的风险（如薪资超预算、技术栈不匹配）。
如果有评估失败的候选人，说明原因。最后提示 HR：可在表格的「决定」列中选择后点击「提交决定」，
或直接回复编号，例如“1、3 进一面，6 淘汰”。不要重复整张表，不要编造数据。"""


def level_of(score: int) -> str:
    return "推荐" if score >= 75 else "待定" if score >= 60 else "不推荐"


def shortlist_mapping(rows: list[dict]) -> dict:
    return {str(i): {"candidate_id": r["候选人ID"], "name": r["姓名"]} for i, r in enumerate(rows, 1)}


def _score(r: dict) -> int:
    try:
        return int(float(r.get("AI评分") or 0))
    except ValueError:
        return 0


async def screen_node(state: TurnState, ctx: Ctx) -> dict:
    hub = ctx.hub
    phase(ctx, "fetch", "正在从 BOSS 直聘拉取简历…")
    data = await run_step(ctx, "拉取 BOSS 直聘投递（MCP）", lambda: hub.call("boss", "list_new_applications"),
                          summarize=lambda r: f"读取 {r['source']}，共 {len(r['applications'])} 条投递")
    if data is SKIPPED:
        data = {"applications": []}

    cands = [{**{BOSS_TO_LEDGER[k]: v for k, v in a.items() if k in BOSS_TO_LEDGER}, "渠道": "BOSS直聘"}
             for a in data["applications"]]
    added = skipped = 0
    if cands:
        res = await run_step(ctx, "写入本地台账（MCP）", lambda: hub.call("ledger", "upsert_candidates", {"candidates": cands}),
                             summarize=lambda r: f"新增 {len(r['added'])} 人，已存在跳过 {len(r['skipped'])} 人")
        if res is not SKIPPED:
            added, skipped = len(res["added"]), len(res["skipped"])
            if added:
                ledger_changed(ctx)
    phase(ctx, "fetch", f"已拉取 {len(cands)} 份简历（新增 {added} 份，已在台账 {skipped} 份）", "complete")

    pending = await run_step(ctx, "读取待筛选候选人", lambda: hub.call("ledger", "list_candidates", {"stage": "待筛选"}),
                             quiet=True)
    pending = [] if pending is SKIPPED else pending["candidates"]

    jobs: dict[str, Any] = {}
    failed: list[dict] = []
    results: list = []
    if pending:
        phase(ctx, "screen", f"正在根据岗位 JD 筛选 {len(pending)} 份简历…")
        for pos in sorted({c["应聘岗位"] for c in pending}):
            jd = await run_step(ctx, f"获取岗位 JD：{pos}", lambda pos=pos: hub.call("boss", "get_job", {"position": pos}),
                                summarize=lambda r: f"{r['department']} · {r['salary_range']}")
            jobs[pos] = "（JD 缺失，请按通用标准评估）" if jd is SKIPPED else jd

        sem = asyncio.Semaphore(3)

        async def evaluate(c: dict):
            async def fn():
                resume = await hub.call("boss", "get_resume", {"application_id": c["候选人ID"]})
                d = await ctx.llm.json(SCORE_SYSTEM, dumps({"岗位要求": jobs.get(c["应聘岗位"]), "候选人简历": resume["resume"]}))
                score = int(d["score"])
                if not 0 <= score <= 100:
                    raise ValueError(f"评分越界：{score}")
                return {"score": score, "reasons": str(d.get("reasons", ""))[:120]}

            async with sem:
                try:
                    r = await run_step(ctx, f"AI 评估：{c['姓名']}（{c['应聘岗位']}）", fn,
                                       summarize=lambda r: f"{r['score']} 分 · {level_of(r['score'])}")
                except StepAborted as e:
                    failed.append({"姓名": c["姓名"], "原因": e.reason, "处置": e.decision.get("message_to_hr", "")})
                    return None
                if r is SKIPPED:
                    failed.append({"姓名": c["姓名"], "原因": "评估失败已跳过，仍保留在「待筛选」"})
                    return None
                return c, r

        results = [x for x in await asyncio.gather(*(evaluate(c) for c in pending)) if x]
        levels = [level_of(r["score"]) for _, r in results]
        summary = "、".join(f"{lv} {levels.count(lv)} 人" for lv in ("推荐", "待定", "不推荐") if levels.count(lv))
        if failed:
            phase(ctx, "screen", f"筛选完成 {len(results)} 人（{summary or '无'}），{len(failed)} 人未完成", "error")
        else:
            phase(ctx, "screen", f"筛选完成：{summary}", "complete")

    if results:
        phase(ctx, "ledger", "正在写入筛选结果到台账…")
        updates = [{"candidate_id": c["候选人ID"], "note": f"AI 初筛 {r['score']} 分",
                    "fields": {"AI评分": r["score"], "AI建议": level_of(r["score"]), "AI理由": r["reasons"], "当前阶段": "待HR决定"}}
                   for c, r in results]
        res = await run_step(ctx, "回写评估结果到台账（MCP）",
                             lambda: hub.call("ledger", "update_candidates", {"updates": updates, "operator": "AI筛选Agent"}),
                             summarize=lambda r: f"{len(r['updated'])} 人进入「待HR决定」")
        if res is not SKIPPED:
            ledger_changed(ctx)
            phase(ctx, "ledger", f"台账已更新：{len(res['updated'])} 人进入「待HR决定」", "complete")

    waiting = await run_step(ctx, "读取待 HR 决策名单", lambda: hub.call("ledger", "list_candidates", {"stage": "待HR决定"}),
                             quiet=True)
    waiting = [] if waiting is SKIPPED else waiting["candidates"]
    if not waiting:
        ctx.emit({"type": "block", "text": "当前没有新的投递，台账中也没有等待 HR 决策的候选人。"})
        return {"shortlist": state.get("shortlist", {})}

    waiting.sort(key=lambda r: (r.get("应聘岗位", ""), -_score(r)))
    mapping = shortlist_mapping(waiting)
    brief = [{"编号": k, "姓名": r["姓名"], "岗位": r["应聘岗位"], "学历/年限": f"{r['学历']}/{r['工作年限']}",
              "期望薪资": r.get("期望薪资"), "评分": _score(r), "建议": r.get("AI建议"), "理由": r.get("AI理由")}
             for k, r in zip(mapping, waiting)]
    ctx.emit({"type": "shortlist", "rows": brief})
    await stream_reply(ctx, "生成初筛总结", SCREEN_SUMMARY_SYSTEM,
                       dumps({"初筛结果": brief, "岗位薪资范围": {p: j.get("salary_range") for p, j in jobs.items() if isinstance(j, dict)},
                              "评估失败": failed}))
    return {"shortlist": mapping, "shortlist_rows": brief}


# ---------------------------------------------------------------- status-update sub-agent

UPDATE_SYSTEM = f"""你是招聘流程助手。把 HR 的指令解析为候选人状态变更，输出 JSON：
{{"actions": [{{"candidate_id": "台账中的候选人ID", "name": "姓名", "target_stage": "目标阶段", "interview_time": "YYYY-MM-DD HH:MM 或空串", "interviewer": "面试官或空串", "note": "简短备注或空串"}}],
 "unresolved": ["无法确定的指令片段及原因"]}}
可选阶段：{"、".join(STAGES)}
映射规则：
- “进一面/约一面/通过初筛/可以聊聊” → 待一面；“一面通过/约二面” → 待二面；“二面通过/约终面” → 待终面
- “终面通过/准备发offer” → 待发Offer；“offer已发” → 已发Offer；“已入职/报到了” → 已入职
- “淘汰/不合适/不考虑” → 已淘汰；“候选人放弃/拒了/不来了” → 已放弃
- 只安排面试时间或面试官、不改变阶段时，target_stage 填该候选人当前阶段
- HR 用编号指代时，按“待决策名单编号”映射；用姓名指代时，从“在途候选人”中匹配
- 面试时间按“当前时间”把“周四下午3点/明天上午10点”换算为绝对时间；没说具体钟点就留空
- 找不到对应候选人、同名无法区分、意思不明确的，放入 unresolved，不要猜"""

UPDATE_SUMMARY_SYSTEM = """你是招聘助手。根据执行结果向 HR 简洁汇报（用简短列表）：
1）已完成的变更：姓名：原阶段 → 新阶段（如有面试时间/面试官一并列出）；
2）未完成的项：原因 + 处置决策/建议 HR 怎么做；
3）无法识别的指令：请 HR 补充什么信息。
没有的部分不要写，不要编造。"""


async def update_node(state: TurnState, ctx: Ctx) -> dict:
    hub = ctx.hub
    decisions = state.get("decisions") or []
    phase(ctx, "parse", "正在核对 HR 的决定…" if decisions else "正在理解 HR 的决定…")
    rows = await run_step(ctx, "读取在途候选人（MCP）", lambda: hub.call("ledger", "list_candidates"),
                          summarize=lambda r: f"{len([c for c in r['candidates'] if c['当前阶段'] not in TERMINAL_STAGES])} 人在途")
    rows = [] if rows is SKIPPED else rows["candidates"]
    by_id = {str(r["候选人ID"]): r for r in rows}
    active = [{"候选人ID": r["候选人ID"], "姓名": r["姓名"], "应聘岗位": r["应聘岗位"], "当前阶段": r["当前阶段"],
               "面试时间": r.get("面试时间", ""), "面试官": r.get("面试官", "")}
              for r in rows if r["当前阶段"] not in TERMINAL_STAGES]

    if decisions:
        plan = {"actions": decisions, "unresolved": []}
    else:
        user = dumps({"当前时间": today(), "待决策名单编号": state.get("shortlist", {}), "在途候选人": active, "HR指令": state["user_text"]})
        plan = await run_step(ctx, "解析 HR 指令", lambda: ctx.llm.json(UPDATE_SYSTEM, user, state.get("history")),
                              summarize=lambda p: f"识别 {len(p.get('actions', []))} 项变更，{len(p.get('unresolved', []))} 项待确认")
        if plan is SKIPPED:
            plan = {"actions": [], "unresolved": [state["user_text"]]}
    n_actions, n_unresolved = len(plan.get("actions", [])), len(plan.get("unresolved", []))
    phase(ctx, "parse", f"识别到 {n_actions} 项变更" + (f"，{n_unresolved} 项需要确认" if n_unresolved else ""), "complete")

    done, failed, unresolved = [], [], list(plan.get("unresolved", []))
    if plan.get("actions"):
        phase(ctx, "update", "正在更新表格状态…")
    for a in plan.get("actions", []):
        cid = str(a.get("candidate_id", ""))
        cur = by_id.get(cid)
        if not cur:
            unresolved.append(f"{a.get('name') or cid}：台账中找不到该候选人")
            continue
        fields = {"当前阶段": a.get("target_stage") or cur["当前阶段"]}
        if a.get("interview_time"):
            fields["面试时间"] = a["interview_time"]
        if a.get("interviewer"):
            fields["面试官"] = a["interviewer"]
        if a.get("note"):
            fields["备注"] = a["note"]
        old, new = cur["当前阶段"], fields["当前阶段"]
        step = f"更新 {cur['姓名']}：{old} → {new}" if new != old else f"更新 {cur['姓名']} 的面试安排"
        payload = {"updates": [{"candidate_id": cid, "fields": fields, "note": a.get("note", "")}], "operator": "HR"}
        try:
            res = await run_step(ctx, step, lambda payload=payload: hub.call("ledger", "update_candidates", payload),
                                 summarize=lambda r: "已写入台账并记录流水")
        except StepAborted as e:
            failed.append({"姓名": cur["姓名"], "目标": new, "原因": e.reason,
                           "处置决策": ACTION_LABEL[e.decision["action"]], "建议": e.decision.get("message_to_hr", "")})
            continue
        if res is SKIPPED:
            failed.append({"姓名": cur["姓名"], "目标": new, "原因": "执行失败，已跳过"})
            continue
        ledger_changed(ctx)
        done.append({"姓名": cur["姓名"], "原阶段": old, "新阶段": new,
                     "面试时间": fields.get("面试时间", ""), "面试官": fields.get("面试官", "")})

    if plan.get("actions"):
        if failed or len(done) < n_actions:
            phase(ctx, "update", f"已更新 {len(done)} 人，{n_actions - len(done)} 项未完成", "error")
        else:
            phase(ctx, "update", f"表格已更新：{len(done)} 人", "complete")

    await stream_reply(ctx, "生成执行汇报", UPDATE_SUMMARY_SYSTEM,
                       dumps({"已完成": done, "未完成": failed, "无法识别": unresolved}))
    return {}


# ---------------------------------------------------------------- query sub-agent

QUERY_SYSTEM = """你是招聘数据助手。只根据提供的台账统计数据回答 HR 的问题，简洁、有数字，可用短列表。
如需提醒，指出近期面试安排和长时间停留在「待HR决定」的候选人。不要编造数据。"""


async def query_node(state: TurnState, ctx: Ctx) -> dict:
    phase(ctx, "read", "正在读取台账…")
    rows = await run_step(ctx, "读取台账（MCP）", lambda: ctx.hub.call("ledger", "list_candidates"),
                          summarize=lambda r: f"{len(r['candidates'])} 名候选人")
    rows = [] if rows is SKIPPED else rows["candidates"]
    logs = await run_step(ctx, "读取最近跟进流水（MCP）", lambda: ctx.hub.call("ledger", "get_logs", {"limit": 10}),
                          summarize=lambda r: f"{len(r['logs'])} 条")
    logs = [] if logs is SKIPPED else logs["logs"]
    phase(ctx, "read", f"已读取台账：{len(rows)} 名候选人", "complete")

    by_stage = {s: sum(1 for r in rows if r["当前阶段"] == s) for s in STAGES}
    by_pos: dict[str, dict[str, int]] = {}
    for r in rows:
        by_pos.setdefault(r["应聘岗位"], {}).setdefault(r["当前阶段"], 0)
        by_pos[r["应聘岗位"]][r["当前阶段"]] += 1
    interviews = [{"姓名": r["姓名"], "阶段": r["当前阶段"], "面试时间": r.get("面试时间"), "面试官": r.get("面试官")}
                  for r in rows if r["当前阶段"] in INTERVIEW_STAGES]
    people = [{"姓名": r["姓名"], "岗位": r["应聘岗位"], "阶段": r["当前阶段"], "AI评分": r.get("AI评分"),
               "面试时间": r.get("面试时间"), "更新时间": r.get("更新时间")} for r in rows]
    await stream_reply(ctx, "生成查询结果", QUERY_SYSTEM,
                       dumps({"当前时间": today(), "问题": state["user_text"], "各阶段人数": by_stage, "各岗位分布": by_pos,
                              "面试安排": interviews, "候选人": people, "最近流水": logs}), state.get("history"))
    return {}


# ---------------------------------------------------------------- fallback

CHAT_SYSTEM = """你是企业微信里的 AI 招聘助手。简短友好地回应 HR，并提示你能做的事：
1）“帮我拉取一下目前投递的简历”——从 BOSS 直聘拉取并 AI 初筛；
2）“1、3 进一面，5 淘汰”——按 HR 决定更新台账；
3）“张伟一面通过，周四下午3点二面，面试官王工”——同步面试进度；
4）“现在招聘进度怎么样？”——查询统计。"""


async def chat_node(state: TurnState, ctx: Ctx) -> dict:
    await stream_reply(ctx, "生成回复", CHAT_SYSTEM, state["user_text"], state.get("history"))
    return {}


# ---------------------------------------------------------------- graph

def _guard(fn):
    # Not functools.wraps: LangGraph inspects the signature to decide whether to pass `config`.
    async def node(state: TurnState, config: RunnableConfig) -> dict:
        ctx: Ctx = config["configurable"]["ctx"]
        try:
            return await fn(state, ctx)
        except StepAborted as e:
            ctx.emit({"type": "block", "text": e.as_markdown()})
            return {"intent": "aborted"}
    return node


def build_graph():
    g = StateGraph(TurnState)
    g.add_node("router", _guard(router_node))
    g.add_node("screen", _guard(screen_node))
    g.add_node("update", _guard(update_node))
    g.add_node("query", _guard(query_node))
    g.add_node("chat", _guard(chat_node))
    g.add_edge(START, "router")
    g.add_conditional_edges("router", lambda s: s.get("intent", "chat"),
                            {"screen": "screen", "update": "update", "query": "query", "chat": "chat", "aborted": END})
    for n in ("screen", "update", "query", "chat"):
        g.add_edge(n, END)
    return g.compile()


GRAPH = build_graph()
