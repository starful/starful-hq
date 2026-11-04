"""CompanyDB content: company pages instead of articles.

companydb.net builds one static page per listed company from filings (script/build_company_page.py):
Japan via EDINET, Korea via Open DART, the US via SEC EDGAR. So here
- 글쓰기 = 회사 추가: a ticker (or a company name Claude turns into one) is queued in data/universe_extra.json
  (add_universe_ticker) and its page is built right away, then logos + the index/compare pages are refreshed.
- 리서치 = which companies to add next, from trend / search keywords and what is already covered.
- 이미지 = company logos (output/assets/logos), filled by the script's own logo step — never drawn.
Runs with `python3` from PATH inside the companydb folder, like okadmin's pipeline does.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from app import sites
from app import snapshot
CATEGORIES = ("JP", "KR", "US")
MARKET_LABEL = {"JP": "일본 (EDINET)", "KR": "한국 (DART)", "US": "미국 (SEC)"}

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
if market == "JP":
    key = b.load_api_key()
    metas = b.find_annual_reports_for_secs(key, [ticker])
    if not metas:
        out(ok=False, error="EDINET에서 최근 유가증권보고서를 찾지 못했어요"); sys.exit(0)
    meta = list(metas.values())[0]
    path = b.build_one(meta, b.download_xbrl_zip(key, meta["docID"]), update_index=False)
elif market == "KR":
    path = b.build_kr_company(ticker, 2024, update_index=False)
else:
    path = b.build_us_company(ticker, update_index=False)
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


def add_company(work, market: str, text: str, row: dict[str, Any] | None = None) -> dict[str, Any]:
    """회사 하나: 큐에 넣고 그 회사 페이지를 바로 만들어요 (+ 로고 · 목록 갱신)."""
    from app.content import jpcontent
    from app.work import workers
    r = row or resolve(market, text)
    if r["ticker"] in covered():
        raise RuntimeError(f"이미 있는 회사예요 ({r['ticker']} · {covered()[r['ticker']]})")
    work.say(f"{MARKET_LABEL[r['market']].split(' ')[0]} {r['ticker']} {r.get('name') or ''} 공시 읽는 중".strip())
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
    return f"""You choose which listed companies to add next to CompanyDB (companydb.net): English pages that explain
annual filings of Japanese (EDINET), Korean (Open DART) and US (SEC) public companies for individual investors.
Recommend {n} companies that people are searching for now, using the keywords below. Mix markets when it fits.

Keywords:
{kw_lines}

Already covered ({len(have)}), do NOT repeat: {", ".join(f"{t} {nm}" for t, nm in list(have.items())[:300])}

Return ONLY JSON:
{{"topics": [{{"market": "JP|KR|US", "ticker": "JP 4-char code / KR 6 digits / US symbol", "name_en": "",
"keyword": "which keyword above", "why": "한국어 한 줄: 왜 지금 이 회사인가"}}]}}"""


def picks_from(data: dict[str, Any]) -> list[dict[str, Any]]:
    have = covered()
    seen: set[str] = set()
    out = []
    for i, item in enumerate((data or {}).get("topics") or []):
        if not isinstance(item, dict):
            continue
        m = str(item.get("market") or "").upper()
        t = str(item.get("ticker") or "").strip().upper()
        if m not in CATEGORIES or not t or t in have or t in seen:
            continue
        seen.add(t)
        name = str(item.get("name_en") or t)
        out.append({"id": f"p{int(time.time())}{i}", "bank": "company", "kind": "회사", "bank_label": MARKET_LABEL[m],
                    "category": m, "row": {"market": m, "ticker": t, "name": name}, "topic": f"{t} {name}",
                    "keyword": str(item.get("keyword") or ""), "why": str(item.get("why") or ""), "status": "new"})
    return out


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
    For CompanyDB these are the ready '추천 회사'."""
    try:
        extra = json.loads((sites.current().repo / "data" / "universe_extra.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(extra, dict):
        return []
    names = extra.get("names") or {}
    have = covered()
    out = []
    for m in CATEGORIES:
        for raw in extra.get(m) or []:
            t = str(raw).strip()
            t = t.zfill(6) if m == "KR" and t.isdigit() else (t.upper() if m == "US" else t)
            if not t or t in have:
                continue
            name = str(names.get(t) or names.get(str(raw)) or "")
            out.append({"id": f"q-{m}-{t}", "bank": "queue", "kind": "회사", "bank_label": f"대기열 · {MARKET_LABEL[m]}", "category": m,
                        "row": {"market": m, "ticker": t, "name": name}, "topic": f"{t} {name}".strip(),
                        "keyword": "", "why": "추가 대기열에 있는데 아직 페이지가 없어요", "status": "new", "queued": True})
    return out
