import os
import tempfile
from datetime import datetime
from pathlib import Path

import pandas as pd
from openpyxl import Workbook, load_workbook
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from .config import CANDIDATE_COLUMNS, LEDGER_PATH, LOG_COLUMNS, PIPELINE, STAGES, TERMINAL_STAGES

SHEET_CANDIDATES = "候选人"
SHEET_LOGS = "跟进流水"


class LedgerLockedError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def init_ledger(force: bool = False, path: Path = LEDGER_PATH) -> None:
    if path.exists() and not force:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET_CANDIDATES
    ws.append(CANDIDATE_COLUMNS)
    ws.freeze_panes = "A2"
    col = get_column_letter(CANDIDATE_COLUMNS.index("当前阶段") + 1)
    dv = DataValidation(type="list", formula1=f'"{",".join(STAGES)}"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add(f"{col}2:{col}2000")
    wb.create_sheet(SHEET_LOGS).append(LOG_COLUMNS)
    _save(wb, path)


def _save(wb: Workbook, path: Path = LEDGER_PATH) -> None:
    # Write to a temp file then swap, so a crash never leaves a half-written ledger.
    fd, tmp = tempfile.mkstemp(suffix=".xlsx", dir=path.parent)
    os.close(fd)
    try:
        wb.save(tmp)
        try:
            os.replace(tmp, path)
        except PermissionError as e:
            raise LedgerLockedError("台账文件被占用（可能正在被 Excel 打开），暂时无法写入") from e
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _load() -> Workbook:
    init_ledger()
    try:
        return load_workbook(LEDGER_PATH)
    except PermissionError as e:
        raise LedgerLockedError("台账文件被占用（可能正在被 Excel 打开），暂时无法读取") from e


def _records(ws) -> list[dict]:
    header = [c.value for c in ws[1]]
    rows = []
    for values in ws.iter_rows(min_row=2, values_only=True):
        if all(v in (None, "") for v in values):
            continue
        rows.append({h: ("" if v is None else v) for h, v in zip(header, values)})
    return rows


def _log_row(cid, name, action, old, new, note, operator) -> list:
    return [_now(), cid, name, action, old, new, note, operator]


def list_candidates(stage: str | None = None, position: str | None = None) -> list[dict]:
    rows = _records(_load()[SHEET_CANDIDATES])
    if stage:
        rows = [r for r in rows if r.get("当前阶段") == stage]
    if position:
        rows = [r for r in rows if position in str(r.get("应聘岗位", ""))]
    return rows


def get_logs(limit: int = 20) -> list[dict]:
    return _records(_load()[SHEET_LOGS])[-limit:]


def upsert_candidates(candidates: list[dict], operator: str = "AI助手") -> dict:
    wb = _load()
    ws, logs = wb[SHEET_CANDIDATES], wb[SHEET_LOGS]
    existing = {str(r["候选人ID"]) for r in _records(ws)}
    added, skipped = [], []
    for c in candidates:
        cid = str(c.get("候选人ID", "")).strip()
        if not cid:
            raise ValueError(f"候选人缺少候选人ID：{c}")
        if cid in existing:
            skipped.append(cid)
            continue
        row = {col: c.get(col, "") for col in CANDIDATE_COLUMNS}
        row["候选人ID"] = cid
        row["当前阶段"] = row["当前阶段"] or "待筛选"
        row["更新时间"] = _now()
        ws.append([row[col] for col in CANDIDATE_COLUMNS])
        logs.append(_log_row(cid, row["姓名"], "新增投递", "", row["当前阶段"], f"来源：{row['渠道'] or '未知'}", operator))
        existing.add(cid)
        added.append(cid)
    if added:
        _save(wb)
    return {"added": added, "skipped": skipped}


def check_transition(name: str, old: str, new: str) -> None:
    if new not in STAGES:
        raise ValueError(f"未知阶段「{new}」，可选：{'、'.join(STAGES)}")
    if new == old:
        return
    if old in TERMINAL_STAGES:
        raise ValueError(f"{name} 已处于终态「{old}」，不能再变更为「{new}」")
    if new in ("已淘汰", "已放弃"):
        return
    if old in PIPELINE and PIPELINE.index(new) < PIPELINE.index(old):
        raise ValueError(f"{name} 当前为「{old}」，流程不允许回退到「{new}」")


def update_candidates(updates: list[dict], operator: str = "AI助手") -> dict:
    wb = _load()
    ws, logs = wb[SHEET_CANDIDATES], wb[SHEET_LOGS]
    cols = {c.value: i + 1 for i, c in enumerate(ws[1])}
    row_of = {
        str(ws.cell(r, cols["候选人ID"]).value): r
        for r in range(2, ws.max_row + 1)
        if ws.cell(r, cols["候选人ID"]).value
    }

    # Validate the whole batch first so a bad item never leaves a partial write.
    planned = []
    for u in updates:
        cid = str(u.get("candidate_id", "")).strip()
        fields = u.get("fields") or {}
        if cid not in row_of:
            raise ValueError(f"台账中不存在候选人 {cid}")
        bad = [k for k in fields if k not in cols or k == "候选人ID"]
        if bad:
            raise ValueError(f"不支持更新的字段：{'、'.join(bad)}")
        r = row_of[cid]
        name = ws.cell(r, cols["姓名"]).value
        old = ws.cell(r, cols["当前阶段"]).value or ""
        new = fields.get("当前阶段", old)
        check_transition(name, old, new)
        planned.append((cid, r, name, old, new, fields, u.get("note", "")))

    for cid, r, name, old, new, fields, note in planned:
        for k, v in fields.items():
            ws.cell(r, cols[k], v)
        ws.cell(r, cols["更新时间"], _now())
        detail = note or "、".join(f"{k}={v}" for k, v in fields.items() if k != "当前阶段")
        logs.append(_log_row(cid, name, "阶段变更" if new != old else "信息更新", old, new, detail, operator))
    if planned:
        _save(wb)
    return {"updated": [{"candidate_id": p[0], "name": p[2], "from": p[3], "to": p[4]} for p in planned]}


def read_frames() -> tuple[pd.DataFrame, pd.DataFrame]:
    init_ledger()
    sheets = pd.read_excel(LEDGER_PATH, sheet_name=None, dtype=str)
    cands = sheets.get(SHEET_CANDIDATES, pd.DataFrame(columns=CANDIDATE_COLUMNS)).fillna("")
    logs = sheets.get(SHEET_LOGS, pd.DataFrame(columns=LOG_COLUMNS)).fillna("")
    return cands, logs
