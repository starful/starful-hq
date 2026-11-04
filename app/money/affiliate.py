"""어필리에이트 현황 (상태 탭). Read-only view of okadmin's numbers — okadmin is not changed.

- okadmin/data/affiliate_reports.json: monthly numbers per platform (楽天 · Agoda · Amazon shown; A8 kept in the
  store but not shown), entered on okadmin 8090 /affiliate. This month vs last month.
- data/affiliate_clicks.jsonl (WORK_ROOT): okpy's own outbound /go/ clicks, last 28 days, when the file exists.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from app import snapshot
# Shown on the card / entry form. A8 is retired from the UI (history stays in okadmin's store).
PLATFORMS = (("rakuten", "楽天", "JPY"), ("agoda", "Agoda", "USD"), ("amazon", "Amazon", "JPY"))
# Platforms save() still accepts, so an A8 number passed in is stored (not shown).
STORE_PLATFORMS = (("rakuten", "楽天", "JPY"), ("a8", "A8.net", "JPY"), ("agoda", "Agoda", "USD"), ("amazon", "Amazon", "JPY"))
EDIT_URL = "http://127.0.0.1:8090/affiliate"


def _reports() -> dict[str, Any]:
    try:
        data = json.loads((snapshot.WORK_ROOT / "okadmin" / "data" / "affiliate_reports.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _money(v: float, cur: str) -> str:
    return f"${v:,.2f}" if cur == "USD" else f"¥{v:,.0f}"


def _outbound(days: int = 28, site: str = "okpy") -> dict[str, Any]:
    path = snapshot.WORK_ROOT / "data" / "affiliate_clicks.jsonl"
    if not path.is_file():
        return {"total": 0, "top": [], "found": False}
    cutoff = datetime.now().astimezone() - timedelta(days=days)
    by: Counter[str] = Counter()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-50000:]
    except OSError:
        return {"total": 0, "top": [], "found": False}
    for line in lines:
        try:
            row = json.loads(line)
            ts = datetime.fromisoformat(str(row.get("ts") or ""))
        except (ValueError, json.JSONDecodeError):
            continue
        if ts.tzinfo is None:
            ts = ts.astimezone()
        if ts < cutoff or str(row.get("site_id") or "") != site:
            continue
        by[str(row.get("banner_id") or "?")] += 1
    return {"total": sum(by.values()), "top": by.most_common(3), "found": True, "days": days}


def summary() -> dict[str, Any]:
    months = (_reports().get("months") or {})
    keys = sorted(k for k in months if isinstance(months.get(k), dict))
    base = {"this_month": datetime.now().strftime("%Y-%m"), "platforms": PLATFORMS, "edit_url": EDIT_URL}
    if not keys:
        return {**base, "month": "", "rows": [], "outbound": _outbound()}
    cur_key = keys[-1]
    prev_key = keys[-2] if len(keys) > 1 else ""
    cur, prev = months[cur_key], months.get(prev_key) or {}
    rows, total_jpy, prev_jpy, updated = [], 0.0, 0.0, ""
    for pid, label, ccy in PLATFORMS:
        m = cur.get(pid) or {}
        p = prev.get(pid) or {}
        com, pcom = float(m.get("commission") or 0), float(p.get("commission") or 0)
        if ccy == "JPY":
            total_jpy += com
            prev_jpy += pcom
        updated = max(updated, str(m.get("updated_at") or ""))
        rows.append({"id": pid, "label": label, "clicks": int(m.get("clicks") or 0), "orders": int(m.get("orders") or 0),
                     "commission": _money(com, ccy), "commission_raw": com, "currency": ccy, "prev": _money(pcom, ccy) if p else "",
                     "prev_clicks": int(p.get("clicks") or 0) if p else None,
                     "note": str(m.get("note") or ""), "earning": com > 0})
    return {**base, "month": cur_key, "prev_month": prev_key, "rows": rows, "total": _money(total_jpy, "JPY"),
            "prev_total": _money(prev_jpy, "JPY") if prev_key else "", "updated": updated[:16],
            "orders": sum(r["orders"] for r in rows), "outbound": _outbound()}


def save(month: str, values: dict[str, dict[str, str]]) -> int:
    """Store this month's numbers through okadmin's own store (same file and history as 8090 /affiliate).
    values: platform id → {clicks, orders, commission}. Only platforms whose numbers changed are written."""
    from app.work import workers
    workers._okadmin_import()
    from affiliate_report_store import save_platform

    cur = {r["id"]: r for r in (summary().get("rows") or [])} if summary().get("month") == month else {}
    changed = 0
    for pid, _label, _ccy in STORE_PLATFORMS:
        v = values.get(pid) or {}
        if not v:
            continue
        try:
            clicks, orders = int(float(v.get("clicks") or 0)), int(float(v.get("orders") or 0))
            commission = round(float(str(v.get("commission") or 0).replace(",", "").replace("¥", "").replace("$", "")), 2)
        except ValueError:
            raise ValueError(f"{pid}: 숫자만 넣어 주세요")
        old = cur.get(pid)
        if old and (old["clicks"], old["orders"], round(old["commission_raw"], 2)) == (clicks, orders, commission):
            continue
        save_platform(pid, month=month, clicks=clicks, orders=orders, commission=commission,
                      note=(old or {}).get("note", ""))
        changed += 1
    return changed
