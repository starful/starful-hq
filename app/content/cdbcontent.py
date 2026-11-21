"""CompanyDB content: company pages instead of articles.

companydb.net builds one static page per listed company from filings (script/build_company_page.py):
Japan via EDINET, Korea via Open DART, the US via SEC EDGAR. So here
- 글쓰기 = 회사 추가: a ticker (or a company name Claude turns into one) is queued in data/universe_extra.json
  (add_universe_ticker) and its page is built right away, then logos + the index/compare pages are refreshed.
- 대기열 = plan staff find_and_queue_companies (no owner typing) → enqueue_ticker; picks = queue + analysis.
- 이미지 = company logos (output/assets/logos), filled by the script's own logo step — never drawn.
Runs with `python3` from PATH inside the companydb folder, like okadmin's pipeline does.
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any

from app import sites
from app import snapshot
CATEGORIES = ("JP", "KR", "US")
MARKET_LABEL = {"JP": "일본 (EDINET)", "KR": "한국 (DART)", "US": "미국 (SEC)"}
# 회사 찾기 목표 비중 (JP 중심 · KR/US도 일정 비율).
MARKET_MIX = {"JP": 0.6, "KR": 0.2, "US": 0.2}
# EDINET scans hundreds of days per company; parallel JP builds exhaust the API.
_JP_BUILD_LOCK = threading.Lock()
_CATALOG_LOCK = threading.Lock()  # catalog/index writes from concurrent markets


def market_quota(n: int) -> dict[str, int]:
    """Split n picks into JP/KR/US by MARKET_MIX (each market ≥1 when n ≥ 3)."""
    n = max(3, min(15, int(n or 10)))
    exact = {m: n * float(MARKET_MIX.get(m) or 0) for m in CATEGORIES}
    base = {m: int(exact[m]) for m in CATEGORIES}
    for m in CATEGORIES:
        if base[m] < 1:
            base[m] = 1
    while sum(base.values()) > n:
        reduced = False
        for m in ("JP", "US", "KR"):  # trim JP first when overweight
            if base[m] > 1 and sum(base.values()) > n:
                base[m] -= 1
                reduced = True
        if not reduced:
            break
    rem = n - sum(base.values())
    order = sorted(CATEGORIES, key=lambda m: (exact[m] - int(exact[m])), reverse=True)
    i = 0
    while rem > 0 and order:
        base[order[i % len(order)]] += 1
        rem -= 1
        i += 1
    return base

_BUILD_CODE = r'''
import json, sys
sys.path.insert(0, "script")
MARK = "__NEXT_RESULT__"
market, ticker, name = json.loads(sys.argv[1])
def out(**kw):
    print(MARK + json.dumps(kw, ensure_ascii=False), flush=True)
import build_company_page as b
if ticker in b.catalog_tickers():
    out(ok=False, error=f"이미 있는 회사예요 ({ticker})"); sys.exit(0)
try:
    print(json.dumps(b.add_universe_ticker(market, ticker, name_en=name or None), ensure_ascii=False), flush=True)
except ValueError as exc:
    print(f"queue: {exc}", flush=True)
try:
    if market == "JP":
        key = b.load_api_key()
        if not key:
            out(ok=False, error="EDINET API 키가 없어요 (companydb .env)"); sys.exit(0)
        metas = b.find_annual_reports_for_secs(key, [ticker])
        if not metas:
            out(ok=False, error="EDINET에서 최근 유가증권보고서를 찾지 못했어요"); sys.exit(0)
        meta = list(metas.values())[0]
        path = b.build_one(meta, b.download_xbrl_zip(key, meta["docID"]), update_index=False)
    elif market == "KR":
        last_err = None
        path = None
        for year in (2024, 2023):
            try:
                path = b.build_kr_company(ticker, year, update_index=False)
                last_err = None
                break
            except RuntimeError as exc:
                last_err = exc
                if "No DART statements" not in str(exc):
                    raise
        if path is None:
            raise last_err or RuntimeError(f"No DART statements for {ticker}")
    else:
        path = b.build_us_company(ticker, update_index=False)
except Exception as exc:
    out(ok=False, error=str(exc).splitlines()[0][:180]); sys.exit(0)
try:
    b.ensure_catalog_logos()
    b.patch_company_pages_logos()
except Exception as exc:
    print(f"logos: {exc}", flush=True)
b.write_index()
title = next((c.get("company_en") for c in json.loads(open("data/catalog.json", encoding="utf-8").read())
              if str(c.get("ticker")) == str(path.stem)), "") or name or ticker
out(ok=True, path=path.name, title=title, ticker=path.stem)
'''

_LOGOS_CODE = r'''
import json, sys
sys.path.insert(0, "script")
MARK = "__NEXT_RESULT__"
import build_company_page as b
res = b.ensure_catalog_logos()
n = b.patch_company_pages_logos()
b.write_index()
print(MARK + json.dumps({"ok": True, "result": res, "patched": n}, ensure_ascii=False, default=str), flush=True)
'''

# One EDINET date-scan for many tickers (same window as build). Used before write_picks.
_EDINET_READY_CODE = r'''
import json, sys
sys.path.insert(0, "script")
MARK = "__NEXT_RESULT__"
tickers = json.loads(sys.argv[1])
def out(**kw):
    print(MARK + json.dumps(kw, ensure_ascii=False), flush=True)
import build_company_page as b
key = b.load_api_key()
if not key:
    out(ok=False, error="EDINET API 키가 없어요 (companydb .env)"); sys.exit(0)
uniq = []
seen = set()
for t in tickers:
    t = str(t or "").strip()
    if not t or t in seen:
        continue
    seen.add(t)
    uniq.append(t)
if not uniq:
    out(ok=True, ready=[], missing=[])
    sys.exit(0)
metas = b.find_annual_reports_for_secs(key, uniq)
ready, missing = [], []
for t in uniq:
    sec = b._normalize_sec(t)
    (ready if sec in metas else missing).append(t)
out(ok=True, ready=ready, missing=missing)
'''


def is_site() -> bool:
    return sites.current().content == "companydb"


def market_of(ticker: str) -> str:
    t = (ticker or "").strip().upper()
    if re.fullmatch(r"\d{6}", t):
        return "KR"
    if re.fullmatch(r"\d{3}[0-9A-Z]", t):
        return "JP"
    return "US"


def catalog() -> list[dict[str, Any]]:
    try:
        data = json.loads((sites.current().repo / "data" / "catalog.json").read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def covered() -> dict[str, str]:
    """ticker → English company name of every page we have."""
    return {str(c.get("ticker")): str(c.get("company_en") or c.get("ticker")) for c in catalog() if c.get("ticker")}


_TICKER = {"JP": r"\d{3}[0-9A-Z]", "KR": r"\d{6}", "US": r"[A-Z]{1,5}([.-][A-Z])?"}
SKIP_PATH_NAME = "universe_skip.json"


def normalize_ticker(market: str, ticker: str) -> str:
    m = (market or "").strip().upper()
    t = str(ticker or "").strip()
    if m == "KR" and t.isdigit():
        return t.zfill(6)
    if m == "US":
        return t.upper()
    if m == "JP" and t.endswith("0") and len(t) == 5 and t[:4].isdigit():
        return t[:4]
    return t.upper() if m == "US" else t


def _skip_path() -> Path:
    return sites.current().repo / "data" / SKIP_PATH_NAME


def load_skip() -> dict[str, Any]:
    """data/universe_skip.json — tickers we won't retry soon (no filings, etc.)."""
    path = _skip_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def skipped_tickers() -> set[str]:
    """All skipped tickers (any market) for quick membership checks."""
    data = load_skip()
    out: set[str] = set()
    for m in CATEGORIES:
        blk = data.get(m)
        if isinstance(blk, dict):
            out.update(str(k) for k in blk.keys())
        elif isinstance(blk, list):
            out.update(str(x) for x in blk)
    return out


def is_skipped(market: str, ticker: str) -> bool:
    m = (market or "").strip().upper()
    t = normalize_ticker(m, ticker)
    if not m or not t or m not in CATEGORIES:
        return False
    blk = load_skip().get(m)
    if isinstance(blk, dict):
        return t in blk or t.upper() in {str(k).upper() for k in blk}
    if isinstance(blk, list):
        return t in [str(x) for x in blk]
    return False


def mark_skipped(
    market: str,
    ticker: str,
    *,
    reason: str = "",
    name: str = "",
) -> bool:
    """Record an unbuildable ticker and return True when newly added (or reason updated)."""
    from app import snapshot

    m = (market or "").strip().upper()
    t = normalize_ticker(m, ticker)
    if m not in CATEGORIES or not t:
        return False
    data = load_skip()
    for cat in CATEGORIES:
        if not isinstance(data.get(cat), dict):
            data[cat] = {} if not isinstance(data.get(cat), list) else {
                str(x): {"reason": "", "at": "", "name": ""} for x in (data.get(cat) or [])
            }
    blk: dict[str, Any] = dict(data[m])
    prev = blk.get(t) if isinstance(blk.get(t), dict) else None
    row = {
        "reason": (reason or (prev or {}).get("reason") or "")[:200],
        "at": snapshot.now_stamp(),
        "name": (name or (prev or {}).get("name") or "")[:120],
    }
    blk[t] = row
    data[m] = blk
    path = _skip_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return True


def _looks_like_ticker(text: str, market: str) -> bool:
    return market in _TICKER and bool(re.fullmatch(_TICKER[market], text.strip().upper()))


def resolve(market: str, text: str) -> dict[str, str]:
    """'7203' / 'Toyota' / '삼성전자' → {market, ticker, name}. Names go through Claude (light)."""
    market = market if market in CATEGORIES else ""
    text = (text or "").strip()
    if market and _looks_like_ticker(text, market):
        return {"market": market, "ticker": text.upper(), "name": ""}
    from app.work import workers
    workers._okadmin_import()
    from llm_claude import MODEL_LIGHT, claude_json

    data = claude_json(
        f"Give the listed company and its stock ticker for: {text}\n"
        f"Market hint: {market or 'any of JP (TSE 4-character code), KR (KRX 6-digit), US (NYSE/Nasdaq symbol)'}\n"
        'Return ONLY JSON: {"market": "JP|KR|US", "ticker": "", "name_en": "official English name"} — '
        'use null for ticker if it is not a listed company.', timeout_sec=60, model=MODEL_LIGHT) or {}
    m = str(data.get("market") or market).upper()
    t = str(data.get("ticker") or "").strip().upper()
    if m not in CATEGORIES or not t:
        raise RuntimeError(f"「{text[:30]}」의 상장 티커를 찾지 못했어요. 티커로 적어 주세요")
    return {"market": m, "ticker": t, "name": str(data.get("name_en") or "")}


def edinet_ready_tickers(work, tickers: list[str]) -> tuple[set[str], list[str]]:
    """One EDINET scan → (tickers with a recent yuho, missing tickers). Empty input → ({}, [])."""
    from app.content import jpcontent
    from app.work import workers

    uniq = []
    seen: set[str] = set()
    for t in tickers:
        t = str(t or "").strip()
        if not t or t in seen:
            continue
        seen.add(t)
        uniq.append(t)
    if not uniq:
        return set(), []
    if work is not None:
        work.say(f"EDINET 공시 확인 중 · JP {len(uniq)}곳")
    with _JP_BUILD_LOCK:
        res = workers._run_okpy(
            work, getattr(work, "role", "seoyeon"), _EDINET_READY_CODE, uniq,
            timeout=60 * 25, python=jpcontent._site_python(),
        )
    ready = {str(t) for t in (res.get("ready") or [])}
    missing = [str(t) for t in (res.get("missing") or []) if str(t)]
    # if API returned ok but lists incomplete, treat unknowns as missing
    for t in uniq:
        if t not in ready and t not in missing:
            missing.append(t)
    return ready, missing


def parse_add_line(line: str) -> dict[str, str] | None:
    """Hub-compatible parse: 'JP 4661' / 'KR 003550' / 'US COST' / bare ticker guess."""
    s = re.sub(r"\s+", " ", (line or "").strip())
    if not s:
        return None
    parts = s.split(" ", 1)
    if len(parts) == 2 and parts[0].upper() in CATEGORIES:
        return {"market": parts[0].upper(), "ticker": parts[1].strip()}
    if re.fullmatch(r"\d{3,5}", s):
        return {"market": "JP", "ticker": s}
    if re.fullmatch(r"\d{6}", s):
        return {"market": "KR", "ticker": s}
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9.-]{0,9}", s):
        return {"market": "US", "ticker": s.upper()}
    return None


def enqueue_ticker(
    market: str,
    ticker: str,
    *,
    name_en: str = "",
    cik: str = "",
    dart_code: str = "",
) -> dict[str, Any]:
    """Append to data/universe_extra.json via companydb --add (build separately)."""
    import subprocess

    from app.content import jpcontent

    m = (market or "").strip().upper()
    t = normalize_ticker(m, ticker)
    if m not in CATEGORIES or not t:
        raise RuntimeError("시장(JP·KR·US)과 티커가 필요해요")
    if is_skipped(m, t):
        raise RuntimeError(f"공시 없어 건너뛴 회사예요 ({m} {t})")
    repo = sites.current().repo
    script = repo / "script" / "build_company_page.py"
    if not script.is_file():
        raise RuntimeError("build_company_page.py 가 없어요")
    cmd = [jpcontent._site_python(), str(script), "--add", t, "--market", m]
    if name_en.strip():
        cmd.extend(["--name", name_en.strip()])
    if cik.strip():
        cmd.extend(["--cik", cik.strip()])
    if dart_code.strip():
        cmd.extend(["--dart-code", dart_code.strip()])
    try:
        proc = subprocess.run(
            cmd, cwd=str(repo), capture_output=True, text=True, timeout=90, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("대기열 추가가 시간 초과됐어요") from exc
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if proc.returncode != 0:
        msg = err or out or "대기열 추가 실패"
        for line in reversed((err or out).splitlines()):
            if line.strip():
                msg = line.strip()
                break
        raise RuntimeError(msg[:500])
    payload: dict[str, Any] = {"ok": True, "market": m, "ticker": t}
    for line in reversed(out.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                payload.update(json.loads(line))
            except json.JSONDecodeError:
                pass
            break
    return payload


def drop_from_queue(market: str, ticker: str) -> bool:
    """Remove a ticker from data/universe_extra.json so it leaves the 추천 회사 queue.

    Hardcoded base universe lists are untouched — only the Hub/extra queue.
    Returns True when something was removed.
    """
    m = (market or "").strip().upper()
    t = normalize_ticker(m, ticker)
    if m not in CATEGORIES or not t:
        return False
    path = sites.current().repo / "data" / "universe_extra.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    bucket = [str(x).strip() for x in (data.get(m) or [])]
    if m == "KR":
        bucket_norm = [x.zfill(6) if x.isdigit() else x for x in bucket]
        if t not in bucket_norm:
            return False
        data[m] = [x for x, n in zip(bucket, bucket_norm) if n != t]
    else:
        if t not in bucket and t.upper() not in [x.upper() for x in bucket]:
            return False
        data[m] = [x for x in bucket if x != t and x.upper() != t.upper()]
    names = dict(data.get("names") or {})
    names.pop(t, None)
    if m == "US":
        (data.setdefault("sec_ciks", {}) or {}).pop(t, None)
    if m == "KR":
        (data.setdefault("dart_codes", {}) or {}).pop(t, None)
    data["names"] = names
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return True


def unbuildable_error(err: str) -> bool:
    """True when this write error means 'leave the add-queue' (no point retrying soon)."""
    low = (err or "").lower()
    return (
        "유가증권보고서" in (err or "")
        or "no dart statements" in low
        or "edinet api" in low
    )


def add_company(work, market: str, text: str, row: dict[str, Any] | None = None) -> dict[str, Any]:
    """회사 하나: 큐에 넣고 그 회사 페이지를 바로 만들어요 (+ 로고 · 목록 갱신)."""
    from app.content import jpcontent
    from app.work import workers
    r = row or resolve(market, text)
    if r["ticker"] in covered():
        raise RuntimeError(f"이미 있는 회사예요 ({r['ticker']} · {covered()[r['ticker']]})")
    work.say(f"{MARKET_LABEL[r['market']].split(' ')[0]} {r['ticker']} {r.get('name') or ''} 공시 읽는 중".strip())
    lock = _JP_BUILD_LOCK if r["market"] == "JP" else _CATALOG_LOCK
    with lock:
        return workers._run_okpy(work, work.role, _BUILD_CODE, [r["market"], r["ticker"], r.get("name", "")],
                                 timeout=60 * 30, python=jpcontent._site_python())


def fill_logos(work) -> str:
    from app.content import jpcontent
    from app.work import workers
    work.say("회사 로고를 채우는 중")
    res = workers._run_okpy(work, work.role, _LOGOS_CODE, [], timeout=60 * 20, python=jpcontent._site_python())
    return f"로고를 채웠어요 · 페이지 {res.get('patched') or 0}개 갱신"


# ---------- research ----------

def research_prompt(keywords: list[dict[str, Any]], n: int) -> str:
    kw_lines = "\n".join(f"- {k['word']} ({k['from']})" for k in keywords[:24])
    have = covered()
    skip = skipped_tickers()
    skip_line = ", ".join(sorted(skip)[:200]) if skip else "(none)"
    quota = market_quota(n)
    quota_line = " · ".join(f"{m} {quota[m]}" for m in CATEGORIES)
    return f"""You choose which listed companies to add next to CompanyDB (companydb.net): English pages that explain
annual filings of Japanese (EDINET), Korean (Open DART) and US (SEC) public companies for individual investors.
Recommend exactly {n} companies that people are searching for now, using the keywords below.

HARD RULE — market mix (counts must match): {quota_line}
(JP ~60% · KR ~20% · US ~20%). Do not dump into Japan only.

Keywords:
{kw_lines}

Already covered ({len(have)}), do NOT repeat: {", ".join(f"{t} {nm}" for t, nm in list(have.items())[:300])}
No usable filings / previously skipped ({len(skip)}), do NOT repeat: {skip_line}

Return ONLY JSON:
{{"topics": [{{"market": "JP|KR|US", "ticker": "JP 4-char code / KR 6 digits / US symbol", "name_en": "",
"keyword": "which keyword above", "why": "한국어 한 줄: 왜 지금 이 회사인가"}}]}}"""


def _pick_row(m: str, t: str, name: str, *, keyword: str = "", why: str = "", idx: int = 0) -> dict[str, Any]:
    return {
        "id": f"p{int(time.time())}{idx}",
        "bank": "company", "kind": "회사", "bank_label": MARKET_LABEL[m],
        "category": m, "row": {"market": m, "ticker": t, "name": name}, "topic": f"{t} {name}",
        "keyword": keyword, "why": why, "status": "new",
    }


def picks_from(data: dict[str, Any], n: int | None = None) -> list[dict[str, Any]]:
    """Parse Claude topics; when n is set, keep MARKET_MIX quotas (JP 60% · KR 20% · US 20%)."""
    have = covered()
    skip = skipped_tickers()
    seen: set[str] = set()
    buckets: dict[str, list[dict[str, Any]]] = {m: [] for m in CATEGORIES}
    quota = market_quota(n) if n is not None else None
    for i, item in enumerate((data or {}).get("topics") or []):
        if not isinstance(item, dict):
            continue
        m = str(item.get("market") or "").upper()
        t = str(item.get("ticker") or "").strip().upper()
        if m == "KR" and t.isdigit():
            t = t.zfill(6)
        if m not in CATEGORIES or not t or t in have or t in skip or t in seen:
            continue
        if quota is not None and len(buckets[m]) >= quota[m]:
            continue
        seen.add(t)
        name = str(item.get("name_en") or t)
        buckets[m].append(_pick_row(
            m, t, name,
            keyword=str(item.get("keyword") or ""),
            why=str(item.get("why") or ""),
            idx=i,
        ))
    if quota is None:
        return [p for m in CATEGORIES for p in buckets[m]]
    # Prefer KR/US/JP round-robin in the returned list (queue_picks order).
    out: list[dict[str, Any]] = []
    order = ("KR", "US", "JP")
    while any(buckets[m] for m in order):
        for m in order:
            if buckets[m]:
                out.append(buckets[m].pop(0))
    return out


def fill_market_gaps(
    picks: list[dict[str, Any]],
    keywords: list[dict[str, Any]],
    n: int,
    claude_json,
    model: str,
) -> list[dict[str, Any]]:
    """Second Claude pass when a market is under MARKET_MIX quota."""
    from collections import Counter

    quota = market_quota(n)
    counts = Counter(str((p.get("row") or {}).get("market") or p.get("category") or "").upper() for p in picks)
    need = {m: max(0, quota[m] - counts.get(m, 0)) for m in CATEGORIES}
    if not any(need.values()):
        return picks
    have = covered()
    skip = skipped_tickers()
    seen = {
        str((p.get("row") or {}).get("ticker") or "").strip().upper()
        for p in picks if (p.get("row") or {}).get("ticker")
    }
    kw_lines = "\n".join(f"- {k['word']} ({k['from']})" for k in keywords[:24])
    need_line = " · ".join(f"{m} +{need[m]}" for m in CATEGORIES if need[m])
    avoid = ", ".join(sorted(have)[:200] + sorted(skip)[:100] + sorted(seen))
    prompt = f"""CompanyDB: fill ONLY the missing market slots. Need: {need_line}.
Keywords:
{kw_lines}
Do NOT repeat: {avoid}
Return ONLY JSON:
{{"topics": [{{"market": "JP|KR|US", "ticker": "...", "name_en": "", "keyword": "", "why": "한국어 한 줄"}}]}}"""
    data = claude_json(prompt, timeout_sec=240, model=model) or {}
    extra = picks_from(data, n=None)
    still = dict(need)
    filled: list[dict[str, Any]] = []
    for p in extra:
        m = str((p.get("row") or {}).get("market") or "").upper()
        t = str((p.get("row") or {}).get("ticker") or "").strip().upper()
        if still.get(m, 0) <= 0 or not t or t in seen or t in have or t in skip:
            continue
        seen.add(t)
        filled.append(p)
        still[m] -= 1
    return picks + filled


def section_of(path: Path) -> str:
    return market_of(path.stem)


def title_of(path: Path) -> str:
    try:
        head = path.read_text(encoding="utf-8", errors="ignore")[:4000]
    except OSError:
        return path.stem
    m = re.search(r"<title>(.*?)</title>", head, re.S | re.I)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else path.stem


def page_path_for_ticker(ticker: str) -> Path | None:
    """output/companies/<TICKER>.html if it exists."""
    t = str(ticker or "").strip().upper()
    if not t:
        return None
    path = sites.current().repo / sites.current().posts_rel / f"{t}.html"
    return path if path.is_file() else None


def resolve_page_from_text(text: str) -> Path | None:
    """Match a company page from free text (ticker, Exxon Mobil, XOM.html, …)."""
    raw = str(text or "")
    if not raw.strip():
        return None
    # explicit ticker.html / companies/TICKER
    m = re.search(r"(?:companies/)?([A-Z0-9]{1,6})\.html", raw, re.I)
    if m:
        hit = page_path_for_ticker(m.group(1))
        if hit:
            return hit
    # US ticker in parentheses: (XOM)
    m = re.search(r"\(([A-Z]{1,5})\)", raw)
    if m:
        hit = page_path_for_ticker(m.group(1))
        if hit:
            return hit
    low = raw.lower()
    for ticker, name in covered().items():
        name_l = str(name or "").lower()
        parts = [p for p in re.split(r"[^a-z0-9]+", name_l) if len(p) >= 3]
        hit_name = bool(name_l and name_l in low) or (len(parts) >= 2 and all(p in low for p in parts[:2]))
        if ticker.lower() in low or hit_name:
            hit = page_path_for_ticker(ticker)
            if hit:
                return hit
    return None


def fix_page_seo(work, path: Path, brief: str = "") -> str:
    """Rewrite <title> + meta description on a CompanyDB HTML page (no markdown SEO path)."""
    from app import tools
    tools.need("claude")
    from llm_claude import MODEL_LIGHT, claude_json

    path = Path(path)
    if not path.is_file():
        raise RuntimeError(f"페이지를 찾지 못했어요 · {path.name}")
    html = path.read_text(encoding="utf-8")
    title_m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    desc_m = re.search(r'<meta\s+name=["\']description["\']\s+content=["\'](.*?)["\']', html, re.I | re.S)
    old_title = re.sub(r"\s+", " ", (title_m.group(1) if title_m else "")).strip()
    old_desc = re.sub(r"\s+", " ", (desc_m.group(1) if desc_m else "")).strip()
    work.say(f"{path.stem} 제목·메타 고치는 중")
    prompt = (
        "You improve SEO title and meta description for a CompanyDB company page (English).\n"
        f"File: {path.name}\n"
        f"Current title: {old_title}\n"
        f"Current description: {old_desc}\n"
        f"Editor brief: {brief or 'Improve CTR for finance search queries; include concrete figures/dates when relevant.'}\n"
        "Return JSON only: {\"title\": \"...\", \"description\": \"...\"}\n"
        "Title ≤ 60 chars when possible; description ≤ 155 chars. Keep the company name. No markdown."
    )
    data = claude_json(prompt, timeout_sec=180, model=MODEL_LIGHT) or {}
    new_title = re.sub(r"\s+", " ", str(data.get("title") or "")).strip()
    new_desc = re.sub(r"\s+", " ", str(data.get("description") or "")).strip()
    if not new_title or not new_desc:
        raise RuntimeError("제목·메타 제안을 받지 못했어요")
    if new_title == old_title and new_desc == old_desc:
        return f"{path.stem} · 바꿀 내용 없음"
    out = html
    if title_m:
        out = out[: title_m.start(1)] + new_title + out[title_m.end(1) :]
    else:
        out = out.replace("<head>", f"<head>\n  <title>{new_title}</title>", 1)
    if desc_m:
        # re-find after title edit may have shifted; apply on current out
        desc2 = re.search(r'(<meta\s+name=["\']description["\']\s+content=["\'])(.*?)(["\'])', out, re.I | re.S)
        if desc2:
            out = out[: desc2.start(2)] + new_desc + out[desc2.end(2) :]
    else:
        out = out.replace("</title>", f"</title>\n  <meta name=\"description\" content=\"{new_desc}\" />", 1)
    path.write_text(out, encoding="utf-8")
    return f"{path.stem} 제목·메타를 고쳤어요"



def queue_picks() -> list[dict[str, Any]]:
    """Companies already queued in data/universe_extra.json (Hub / CLI / 8090) that have no page yet.
    For CompanyDB these are the ready '추천 회사'.

    Round-robin JP/KR/US so a 15-pick batch is not almost all Japan (EDINET is slow and
    fails often when three writers scan it at once).
    """
    try:
        extra = json.loads((sites.current().repo / "data" / "universe_extra.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(extra, dict):
        return []
    names = extra.get("names") or {}
    have = covered()
    skip = skipped_tickers()
    by_m: dict[str, list[dict[str, Any]]] = {m: [] for m in CATEGORIES}
    for m in CATEGORIES:
        for raw in extra.get(m) or []:
            t = str(raw).strip()
            t = t.zfill(6) if m == "KR" and t.isdigit() else (t.upper() if m == "US" else t)
            if not t or t in have or t in skip:
                continue
            name = str(names.get(t) or names.get(str(raw)) or "")
            by_m[m].append({
                "id": f"q-{m}-{t}", "bank": "queue", "kind": "회사",
                "bank_label": f"대기열 · {MARKET_LABEL[m]}", "category": m,
                "row": {"market": m, "ticker": t, "name": name}, "topic": f"{t} {name}".strip(),
                "keyword": "", "why": "추가 대기열에 있는데 아직 페이지가 없어요",
                "status": "new", "queued": True,
            })
    # KR · US first in each round (DART/SEC), then JP — fewer empty EDINET misses up front.
    order = ("KR", "US", "JP")
    out: list[dict[str, Any]] = []
    i = 0
    while True:
        added = False
        for m in order:
            if i < len(by_m[m]):
                out.append(by_m[m][i])
                added = True
        if not added:
            break
        i += 1
    return out
