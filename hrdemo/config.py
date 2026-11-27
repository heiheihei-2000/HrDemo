import os
from pathlib import Path

import truststore
from dotenv import load_dotenv

# Trust the Windows certificate store so corporate TLS-inspecting proxies are accepted.
truststore.inject_into_ssl()

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env", override=True)

DATA_DIR = ROOT / "data"
LEDGER_PATH = DATA_DIR / "招聘台账.xlsx"
MOCK_DIR = ROOT / "mock_data"
RESUME_DIR = MOCK_DIR / "resumes"
JOBS_PATH = MOCK_DIR / "jobs.json"
MCP_SERVER_DIR = ROOT / "mcp_servers"

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip()
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat").strip()

PIPELINE = ["待筛选", "待HR决定", "待一面", "待二面", "待终面", "待发Offer", "已发Offer", "已入职"]
STAGES = PIPELINE + ["已淘汰", "已放弃"]
TERMINAL_STAGES = {"已入职", "已淘汰", "已放弃"}
INTERVIEW_STAGES = {"待一面", "待二面", "待终面"}

CANDIDATE_COLUMNS = [
    "候选人ID", "姓名", "性别", "年龄", "学历", "院校", "工作年限", "当前公司", "应聘岗位",
    "期望薪资", "手机号", "渠道", "投递时间", "AI评分", "AI建议", "AI理由", "当前阶段",
    "面试时间", "面试官", "备注", "更新时间",
]
LOG_COLUMNS = ["时间", "候选人ID", "姓名", "动作", "原阶段", "新阶段", "说明", "操作人"]
