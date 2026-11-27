"""Stand-in for a recruiting-platform MCP: serves BOSS 直聘 export files from mock_data/."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp.server.fastmcp import FastMCP  # noqa: E402
from openpyxl import load_workbook  # noqa: E402

from hrdemo.config import JOBS_PATH, MOCK_DIR, RESUME_DIR  # noqa: E402

mcp = FastMCP("boss-zhipin-mock", log_level="WARNING")


@mcp.tool()
def list_new_applications(position: str = "") -> dict:
    """读取最新一份 BOSS 直聘导出文件，返回投递记录，可按投递职位过滤。"""
    files = sorted(MOCK_DIR.glob("boss_export_*.xlsx"))
    if not files:
        raise FileNotFoundError("mock_data 下未找到 BOSS 直聘导出文件（boss_export_*.xlsx），请先运行 scripts/make_mock_data.py")
    ws = load_workbook(files[-1], read_only=True).active
    rows = list(ws.iter_rows(values_only=True))
    header = rows[0]
    apps = [{h: ("" if v is None else v) for h, v in zip(header, r)} for r in rows[1:] if any(r)]
    if position:
        apps = [a for a in apps if position in str(a.get("投递职位", ""))]
    return {"source": files[-1].name, "applications": apps}


@mcp.tool()
def get_resume(application_id: str) -> dict:
    """按投递编号获取简历全文。"""
    path = RESUME_DIR / f"{application_id}.txt"
    if not path.exists():
        raise FileNotFoundError(f"未找到投递编号 {application_id} 的简历附件")
    return {"application_id": application_id, "resume": path.read_text(encoding="utf-8")}


@mcp.tool()
def get_job(position: str) -> dict:
    """获取岗位 JD（任职要求、薪资范围）。"""
    jobs = json.loads(JOBS_PATH.read_text(encoding="utf-8"))
    if position not in jobs:
        raise KeyError(f"未找到岗位「{position}」的 JD，现有岗位：{'、'.join(jobs)}")
    return {"position": position, **jobs[position]}


if __name__ == "__main__":
    mcp.run()
