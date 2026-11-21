"""Content adapters for Starful HQ.

sites.current().content picks one kit. Callers use this module instead of
`if content == "poi"` / `_cdb()` / `_jp()` trees.

  okpy_posts     — okpy generate_posts (default path still in workers for CLI code)
  campus_guides  — jpcontent.py
  companydb      — cdbcontent.py
  poi            — poicontent.py
  ""             — site listed but not connected (can_write False)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from app import sites
KINDS = ("okpy_posts", "campus_guides", "companydb", "poi")
# HQ 기획·쓰기로 새로 추가 가능한 okpy 카테고리만 (사이트에 폴더는 더 있어도 여기 없으면 추천·쓰기 안 함).
# data-analysis = Business Analysis · 초안은 영어, 일본어화는 별 잡.
OKPY_CATEGORIES = (
    "python", "cloud", "terraform", "dev-method",
    "data-analysis", "eng-comms", "ai-models", "career",
)
OKPY_ARCHIVE_ONLY = frozenset({"data-model", "pmbok", "agile-scrum", "fit-journey"})
# Japanese Trends/write seeds per writable category (chip pool · research).
OKPY_CATEGORY_SEEDS: dict[str, tuple[str, ...]] = {
    "python": (
        "Python ライブラリ", "FastAPI", "pandas", "NumPy", "Pydantic",
    ),
    "cloud": (
        "AWS GCP Azure 比較", "Kubernetes", "Docker", "サーバレス 比較", "マネージド DB 比較",
    ),
    "terraform": (
        "Terraform", "OpenTofu", "Terraform モジュール", "Terraform state", "IaC 比較",
    ),
    "dev-method": (
        "開発方法論", "CI/CD", "テスト戦略", "コードレビュー", "オブザーバビリティ",
    ),
    "data-analysis": (
        "A/Bテスト", "効果量", "統計的有意差", "実験設計", "メトリクス 設計",
    ),
    "eng-comms": (
        "エンジニア コミュニケーション", "要件のすり合わせ", "ステークホルダー合意", "技術提案の伝え方",
    ),
    "ai-models": (
        "LLM 比較", "Claude vs GPT", "プロンプトエンジニアリング", "Gemini 比較",
    ),
    "career": (
        "エンジニア キャリア", "技術選定 キャリア", "シニアエンジニア", "転職 ポートフォリオ",
    ),
}


def okpy_seed_keywords(limit: int = 20) -> list[str]:
    """Round-robin JA seeds across OKPY_CATEGORIES (no archive / no KO·EN)."""
    pool: list[str] = []
    seen: set[str] = set()
    lists = [list(OKPY_CATEGORY_SEEDS.get(c) or ()) for c in OKPY_CATEGORIES]
    i = 0
    while len(pool) < max(1, int(limit or 20)):
        progressed = False
        for rows in lists:
            if i >= len(rows):
                continue
            w = str(rows[i]).strip()
            key = w.lower()
            if w and key not in seen:
                seen.add(key)
                pool.append(w)
                progressed = True
                if len(pool) >= limit:
                    return pool
            else:
                progressed = True
        if not progressed:
            break
        i += 1
    return pool


def kind() -> str:
    return sites.current().content or ""


def known() -> bool:
    return kind() in KINDS


def is_poi() -> bool:
    return kind() == "poi"


def is_campus() -> bool:
    return kind() == "campus_guides"


def is_companydb() -> bool:
    return kind() == "companydb"


def is_okpy() -> bool:
    return kind() == "okpy_posts"


def uses_topic_bank() -> bool:
    """okpy only: okadmin topic_bank priority_enqueue / refresh."""
    return kind() == "okpy_posts"


def uses_editorial_keywords() -> bool:
    return kind() == "okpy_posts"


def categories() -> tuple[str, ...]:
    k = kind()
    if k == "campus_guides":
        from app.content import jpcontent
        return jpcontent.CATEGORIES
    if k == "companydb":
        from app.content import cdbcontent
        return cdbcontent.CATEGORIES
    if k == "poi":
        from app.content import poicontent
        return poicontent.CATEGORIES
    return OKPY_CATEGORIES


def resolve_write_category(category: str | None, topic: str) -> str:
    """Normalize write category for the current kit (poi: guide-shaped topics → 가이드)."""
    cats = categories()
    if is_poi():
        from app.content import poicontent
        return poicontent.resolve_category(category, topic)
    if category in cats:
        return category
    return cats[0] if cats else str(category or "")


def write(work, category: str, topic: str, row: dict | None = None) -> dict[str, Any] | None:
    """Kit write path. None → okpy_posts; workers runs generate_posts."""
    k = kind()
    if k == "companydb":
        from app.content import cdbcontent
        return cdbcontent.add_company(work, category, topic, row=row)
    if k == "campus_guides":
        from app.content import jpcontent
        return jpcontent.write_guide(work, category, topic, row=row)
    if k == "poi":
        from app.content import poicontent
        return poicontent.write(work, category, topic, row=row)
    return None


def write_done(result: dict[str, Any]) -> str:
    """Korean one-liner after a successful kit write."""
    k = kind()
    title = result.get("title") or result.get("topic") or ""
    if k == "companydb":
        return f"「{title}」 페이지를 만들었어요"
    if k == "campus_guides":
        from app.content import jpcontent
        return f"「{title}」을 썼어요" + (f" ({jpcontent.kit().lang_ko}판 포함)" if result.get("kr") else "")
    if k == "poi":
        from app.content import poicontent
        langs = [poicontent.LANG_KO[x] for x in (result.get("langs") or []) if x != "en"]
        return (f"「{title}」을 썼어요" + (f" ({'·'.join(langs)}판 포함)" if langs else "")
                + (f" · {poicontent.kit().noun_ko} 사진도 받았어요" if result.get("photo") else ""))
    return f"「{title}」을 썼어요"


def research_say(n_keywords: int) -> str:
    k = kind()
    if k == "companydb":
        return "추가할 회사 고르는 중"
    if k == "poi":
        from app.content import poicontent
        return f"키워드 {n_keywords}개로 추가할 {poicontent.kit().noun_ko}·가이드 고르는 중"
    if k == "campus_guides":
        return f"키워드 {n_keywords}개로 가이드 주제 고르는 중"
    return f"키워드 {n_keywords}개로 주제 고르는 중"


def research_picks(keywords: list[dict[str, Any]], n_topics: int, claude_json) -> list[dict[str, Any]] | None:
    """Kit research via Claude. None → caller runs the okpy bank prompt."""
    from llm_claude import MODEL_HEAVY

    k = kind()
    if k == "companydb":
        from app.content import cdbcontent
        picks = cdbcontent.picks_from(
            claude_json(cdbcontent.research_prompt(keywords, n_topics), timeout_sec=300, model=MODEL_HEAVY) or {},
            n=n_topics,
        )
        return cdbcontent.fill_market_gaps(picks, keywords, n_topics, claude_json, MODEL_HEAVY)
    if k == "poi":
        from app.content import poicontent
        return poicontent.picks_from(claude_json(poicontent.research_prompt(keywords, n_topics), timeout_sec=300, model=MODEL_HEAVY) or {})
    if k == "campus_guides":
        from app.content import jpcontent
        return jpcontent.picks_from(claude_json(jpcontent.research_prompt(keywords, n_topics), timeout_sec=300, model=MODEL_HEAVY) or {})
    return None


def enrich_research(data: dict[str, Any]) -> dict[str, Any]:
    """CompanyDB: merge add-queue into picks for the UI."""
    if kind() != "companydb":
        return data
    from app.content import cdbcontent
    mine = data.get("picks") or []
    seen = {str((p.get("row") or {}).get("ticker")) for p in mine}
    return {**data, "picks": mine + [q for q in cdbcontent.queue_picks() if q["row"]["ticker"] not in seen]}


def redraw_blocked() -> str | None:
    """If redraw is not allowed, Korean reason; else None."""
    if kind() == "companydb":
        return "CompanyDB 이미지는 회사 로고예요. '로고 채우기'로 받아요 (그리지 않아요)"
    return None


def section_of(path: Path) -> str | None:
    """Kit-specific section, or None to use snapshot's flat/folder default."""
    if kind() == "companydb":
        from app.content import cdbcontent
        return cdbcontent.section_of(path)
    if kind() == "poi":
        from app.content import poicontent
        return "guide" if path.parent.name == "guides" else poicontent.kit().section
    return None


def slug_of(url: str) -> str | None:
    """Kit-specific URL→stem, or None to use marketing's flat/blog default."""
    path = unquote(urlparse(str(url or "")).path or str(url or ""))
    path = path.split("#", 1)[0].rstrip("/")
    if kind() == "companydb":
        last = [s for s in path.split("/") if s][-1:] or [""]
        return last[0][:-5] if last[0].endswith(".html") else last[0]
    if kind() == "poi":
        segs = [s for s in path.split("/") if s]
        stem = segs[-1] if segs else ""
        if stem and not stem.endswith(("_en", "_ko", "_ja")):
            query = urlparse(str(url or "")).query or ""
            stem += "_ko" if "lang=ko" in query else ("_ja" if "lang=ja" in query else "_en")
        return stem
    return None


# ----- write UI hints (seed / topics placeholders) -----

_OKPY_SEED = "pandas, FastAPI"
_OKPY_TOPICS = "pandas로 엑셀 여러 시트 합치기\nFastAPI 백그라운드 작업"
_CDB_SEED = "決算, 配当 銘柄"
_CDB_TOPICS = "7203\nSamsung Electronics\nNVDA"
_CAMPUS_TRANSLATION = ("_kr", "_ja")
_POI_TRANSLATION = ("_ko", "_ja")


def seed_hint(site: sites.Site | None = None) -> str:
    s = site or sites.current()
    if s.content == "poi":
        from app.content import poicontent
        k = poicontent.KITS.get(s.id)
        return (k.seed_hint if k else "") or _OKPY_SEED
    if s.content == "campus_guides":
        from app.content import jpcontent
        k = jpcontent.KITS.get(s.id)
        return (k.seed_hint if k else "") or _OKPY_SEED
    if s.content == "companydb":
        return _CDB_SEED
    return _OKPY_SEED


def topics_hint(site: sites.Site | None = None) -> str:
    s = site or sites.current()
    if s.content == "poi":
        from app.content import poicontent
        k = poicontent.KITS.get(s.id)
        return (k.topics_hint if k else "") or _OKPY_TOPICS
    if s.content == "campus_guides":
        from app.content import jpcontent
        k = jpcontent.KITS.get(s.id)
        return (k.topics_hint if k else "") or _OKPY_TOPICS
    if s.content == "companydb":
        return _CDB_TOPICS
    return _OKPY_TOPICS


# ----- snapshot layout (posts / translations / guides folder) -----

def translation_suffixes() -> tuple[str, ...]:
    return _POI_TRANSLATION if is_poi() else _CAMPUS_TRANSLATION


def is_translation(path: Path) -> bool:
    return path.stem.endswith(translation_suffixes())


def post_rel(path: Path, *, flat: bool) -> str:
    if is_poi() and path.parent.name == "guides":
        return f"guides/{path.name}"
    return path.name if flat else f"{path.parent.name}/{path.name}"


def list_posts(root: Path, ext: str) -> list[Path]:
    if not root.is_dir():
        return []
    if is_poi():
        return sorted(root.glob(f"*{ext}")) + sorted(root.glob(f"guides/*{ext}"))
    flat = sites.current().flat
    return sorted(root.glob(f"*{ext}" if flat else f"*/*{ext}"))


def safe_post_home(path: Path, root: Path, *, flat: bool) -> Path:
    """Directory that must equal posts root for safe_post checks."""
    if is_poi() and path.parent.name == "guides":
        return path.parent.parent
    return path.parent if flat else path.parent.parent


def image_noun() -> str:
    """Label for an image that belongs to a place page (not a guide)."""
    if is_poi():
        from app.content import poicontent
        return poicontent.kit().noun_ko
    return sites.current().words["noun"]


def image_base_stem(stem: str) -> str:
    if is_poi():
        from app.content import poicontent
        return poicontent.base_of(stem)
    return stem


def link_place_images(path: Path, out: dict[str, dict[str, str]], title: str, rel: str, image_ext: tuple[str, ...]) -> None:
    """Register shop.jpg ↔ shop_en.md pairs (poi) or guide_x.jpg ↔ guide_x.md (campus flat)."""
    if is_poi() and path.parent.name != "guides":
        from app.content import poicontent
        for ext in image_ext:
            out.setdefault(poicontent.base_of(path.stem) + ext, {"title": title, "rel": rel})
    elif sites.current().flat and not path.stem.endswith(_CAMPUS_TRANSLATION):
        for ext in image_ext:
            out.setdefault(path.stem + ext, {"title": title, "rel": rel})
