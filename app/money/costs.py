"""💰 GCP 비용 (Gemini · Imagen · Banana · Places · 그 외) and 하루 상한.

- Numbers: okadmin's GCP Billing reader (ai_spend.py, BigQuery). This month from okadmin cache; past months in
  data/costs/<month>.json.
- 상한 (data/spend_limits.json): AI 그림 · Places · YouTube 검색(일) + 그림·사진 월 ¥.
  `take(kind)` before each use; over limit → OverLimit ("내일 이어서"). YouTube is free-quota only (no monthly ¥).
"""

from __future__ import annotations
from app.paths import HQ_ROOT

import json
import threading
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

ROOT = HQ_ROOT  # starful-hq/ (data lives next to app/)
LIMITS_PATH = ROOT / "data" / "spend_limits.json"
COUNT_PATH = ROOT / "data" / "spend_counter.json"
MONTHS_DIR = ROOT / "data" / "costs"
DEFAULT_LIMITS = {
    "daily_images": 10,
    "daily_places": 30,
    "daily_youtube": 80,  # YouTube search.list ≈ 100 units; free quota ~10k/day
    "monthly_yen": 3000,
}
# kind → (limit key, label, counts toward monthly ¥)
KIND_META = {
    "image": ("daily_images", "AI 그림", True),
    "places": ("daily_places", "Places 사진", True),
    "youtube": ("daily_youtube", "YouTube 검색", False),
}
KINDS = (("gemini", "Gemini 글"), ("imagen", "Imagen 그림"), ("banana", "Gemini 그림"), ("places", "Places 사진"))
PAID = ("imagen", "banana", "places")   # what the month budget covers
_LOCK = threading.Lock()


class OverLimit(RuntimeError):
    pass


# ---------- limits + today's counter ----------

def _read(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def limits() -> dict[str, int]:
    saved = _read(LIMITS_PATH)
    return {k: int(saved.get(k, v)) for k, v in DEFAULT_LIMITS.items()}


def set_limits(**values: Any) -> dict[str, int]:
    cur = limits()
    for k in DEFAULT_LIMITS:
        if values.get(k) not in (None, ""):
            cur[k] = max(0, int(float(values[k])))
    _write(LIMITS_PATH, cur)
    return cur


def today_counts() -> dict[str, int]:
    data = _read(COUNT_PATH)
    day = data.get(date.today().isoformat()) or {}
    return {
        "image": int(day.get("image") or 0),
        "places": int(day.get("places") or 0),
        "youtube": int(day.get("youtube") or 0),
    }


def take(kind: str, n: int = 1) -> None:
    """Count today's usage for image / places / youtube; OverLimit when it would go over."""
    meta = KIND_META.get(kind)
    if not meta:
        raise ValueError(f"unknown spend kind: {kind}")
    lim_key, label, month_paid = meta
    lim = limits()
    with _LOCK:
        cap = int(lim.get(lim_key) or 0)
        used = today_counts()[kind]
        if used + n > cap:
            unit = "회" if kind in ("places", "youtube") else "장"
            raise OverLimit(f"오늘 {label} 상한 {cap}{unit}에 닿았어요 ({used}{unit} 씀) · 내일 이어서 할게요")
        if month_paid:
            month = month_paid_yen()
            if lim["monthly_yen"] and month is not None and month >= lim["monthly_yen"]:
                raise OverLimit(f"이번 달 그림·사진 비용 ¥{month:,}이 상한 ¥{lim['monthly_yen']:,}에 닿았어요")
        data = _read(COUNT_PATH)
        today = date.today().isoformat()
        day = data.get(today) or {}
        day[kind] = int(day.get(kind) or 0) + n
        data = {k: v for k, v in data.items() if k >= (date.today().replace(day=1)).isoformat()}   # keep this month
        data[today] = day
        _write(COUNT_PATH, data)


def can_take(kind: str, n: int = 1) -> bool:
    meta = KIND_META.get(kind)
    if not meta:
        return False
    lim_key, _, _ = meta
    lim = limits()
    return today_counts()[kind] + n <= int(lim.get(lim_key) or 0)


def left(kind: str) -> int:
    meta = KIND_META.get(kind)
    if not meta:
        return 0
    lim_key, _, _ = meta
    lim = limits()
    return max(0, int(lim.get(lim_key) or 0) - today_counts()[kind])


def cap_rows() -> list[dict[str, Any]]:
    """Rows for the cost-card limit strip (used / cap / warn level)."""
    lim = limits()
    used = today_counts()
    rows = []
    for kind, (lim_key, label, _) in KIND_META.items():
        cap = int(lim.get(lim_key) or 0)
        n = int(used.get(kind) or 0)
        pct = (100 * n / cap) if cap else 0
        level = "over" if cap and n >= cap else "danger" if pct >= 90 else "warn" if pct >= 70 else ""
        rows.append({
            "kind": kind,
            "label": label.replace("AI ", "").replace(" 사진", "").replace(" 검색", ""),
            "used": n,
            "cap": cap,
            "pct": min(100, round(pct)),
            "level": level,
            "unit": "회" if kind in ("places", "youtube") else "장",
        })
    return rows


# ---------- GCP billing ----------

def _okadmin():
    from app.work import workers
    workers._okadmin_import()
    import ai_spend

    return ai_spend


def _yen(block: dict[str, Any] | None, currency: str) -> int:
    v = float((block or {}).get("cost") or 0)
    rate = 1.0 if (currency or "").upper() == "JPY" else 150.0
    return int(round(v * rate))


def _shape(summary: dict[str, Any]) -> dict[str, Any]:
    cur = summary.get("currency") or "JPY"
    cats = {k: _yen(summary.get(k), cur) for k, _ in KINDS}
    total = _yen(summary.get("total"), cur)
    daily = [{"date": d.get("date"), "yen": int(round(float(d.get("cost") or 0) * (1 if cur.upper() == "JPY" else 150)))}
             for d in (summary.get("total") or {}).get("daily") or []]
    return {"month": summary.get("month"), "cats": cats, "other": max(0, total - sum(cats.values())), "total": total,
            "paid": sum(cats[k] for k in PAID), "daily": daily, "note": summary.get("note") or "",
            "error": summary.get("error") or "", "updated": str(summary.get("updated_at") or "")[:16].replace("T", " ")}


def _past_month(month: str) -> dict[str, Any] | None:
    """A closed month, read from BigQuery at most once a day."""
    path = MONTHS_DIR / f"{month}.json"
    saved = _read(path)
    if saved and time.time() - float(saved.get("_at") or 0) < 24 * 3600:
        return saved.get("summary")
    try:
        sp = _okadmin()
        client = sp._bq_client()
        table = sp._resolve_table(client, sp._DEFAULT_PROJECT, sp._DEFAULT_DATASET)
        if not table:
            return None
        fqn = f"{sp._DEFAULT_PROJECT}.{sp._DEFAULT_DATASET}.{table}"
        rows = sp._query_month_costs(client, fqn, month, sp._DEFAULT_PROJECT)["rows"]
        summary = sp._build_summary_from_rows(month, rows, note="GCP Billing (BigQuery)", table=fqn)
    except Exception:  # noqa: BLE001 - shown as '-' on the card
        return saved.get("summary") if saved else None
    _write(path, {"_at": time.time(), "summary": summary})
    return summary


def _prev(month: str, n: int) -> str:
    y, m = map(int, month.split("-"))
    m -= n
    while m <= 0:
        m += 12
        y -= 1
    return f"{y}-{m:02d}"


_MONTH_CACHE: dict[str, Any] = {}


def month_paid_yen() -> int | None:
    """This month's Imagen + Gemini image + Places cost (yen), from the last billing read (no new query)."""
    s = _MONTH_CACHE.get("summary")
    if s is None:
        try:
            raw = json.loads((_okadmin().CACHE_PATH).read_text(encoding="utf-8")).get("summary") or {}
        except Exception:  # noqa: BLE001
            return None
        if raw.get("month") != datetime.now().strftime("%Y-%m"):
            return None
        s = _shape(raw)
    return s["paid"]


def claude_limits() -> dict[str, Any]:
    """Claude subscription windows (5시간 · 주간) the Mac's Claude Code login sees — okadmin's reader, cached 5 min."""
    try:
        from app.work import workers
        workers._okadmin_import()
        import claude_usage

        s = claude_usage.usage_summary() or {}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:160], "windows": []}
    return {"ok": bool(s.get("ok")), "error": s.get("error") or "", "plan": s.get("subscription_type") or "",
            "windows": [{"label": w.get("label"), "percent": float(w.get("percent") or 0), "level": w.get("level") or "ok",
                         "resets": w.get("resets_local") or "", "resets_in": w.get("resets_in") or ""}
                        for w in (s.get("windows") or []) if isinstance(w, dict)]}


def summary(with_past: bool = True) -> dict[str, Any]:
    """The 💰 card: Claude limits, this month's GCP (+ the two before), today's counters and the limits."""
    out: dict[str, Any] = {
        "limits": limits(),
        "today": today_counts(),
        "caps": cap_rows(),
        "months": [],
        "error": "",
        "claude": claude_limits(),
    }
    try:
        now = _shape(_okadmin().spend_summary())
        _MONTH_CACHE["summary"] = now
        out["now"] = now
        if now.get("error"):
            out["error"] = now["error"]
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)[:200]
        out["now"] = None
    if with_past and out.get("now"):
        for n in (1, 2):
            m = _prev(out["now"]["month"], n)
            s = _past_month(m)
            out["months"].append(_shape(s) if s else {"month": m, "total": None})
    return out


# ---------- cached card (switching site tabs must not re-read BigQuery / Claude each time) ----------

CARD_MAX_AGE = 10 * 60
_CARD: dict[str, Any] = {"at": 0.0, "data": None}
_CARD_LOCK = threading.Lock()


def _with_live_counts(data: dict[str, Any]) -> dict[str, Any]:
    out = dict(data)
    out["limits"], out["today"] = limits(), today_counts()   # cheap, local: always current
    out["caps"] = cap_rows()
    return out


def cached() -> tuple[dict[str, Any] | None, bool]:
    """(last card data or None, is it stale?) — never reads anything slow."""
    data = _CARD["data"]
    if data is None:
        return None, True
    return _with_live_counts(data), time.time() - _CARD["at"] > CARD_MAX_AGE


def summary_cached(fresh: bool = False) -> dict[str, Any]:
    data, stale = cached()
    if data is not None and not stale and not fresh:
        return data
    with _CARD_LOCK:
        if not fresh and _CARD["data"] is not None and time.time() - _CARD["at"] <= CARD_MAX_AGE:
            return _with_live_counts(_CARD["data"])
        data = summary()
        _CARD["data"], _CARD["at"] = data, time.time()
    out = _with_live_counts(data)
    return out


def card_age() -> str:
    if not _CARD["at"]:
        return ""
    return datetime.fromtimestamp(_CARD["at"]).strftime("%H:%M")
