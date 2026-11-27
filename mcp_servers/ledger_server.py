import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp.server.fastmcp import FastMCP  # noqa: E402

from hrdemo import ledger  # noqa: E402

mcp = FastMCP("local-excel-ledger", log_level="WARNING")


@mcp.tool()
def list_candidates(stage: str = "", position: str = "") -> dict:
    """查询本地招聘台账中的候选人，可按当前阶段、应聘岗位过滤。"""
    return {"candidates": ledger.list_candidates(stage or None, position or None)}


@mcp.tool()
def upsert_candidates(candidates: list[dict], operator: str = "AI助手") -> dict:
    """批量新增候选人（按候选人ID去重，已存在的跳过）。字段名与台账列名一致。"""
    return ledger.upsert_candidates(candidates, operator)


@mcp.tool()
def update_candidates(updates: list[dict], operator: str = "AI助手") -> dict:
    """批量更新候选人。updates 每项为 {candidate_id, fields: {列名: 值}, note}。
    变更「当前阶段」会校验流程规则（不可回退、终态不可变更），整批校验通过才写入，并记录跟进流水。"""
    return ledger.update_candidates(updates, operator)


@mcp.tool()
def get_logs(limit: int = 20) -> dict:
    """获取最近的跟进流水。"""
    return {"logs": ledger.get_logs(limit)}


if __name__ == "__main__":
    ledger.init_ledger()
    mcp.run()
