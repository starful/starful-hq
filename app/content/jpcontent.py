"""Campus sites' content (JP Campus · KR Campus): research, new guides (English + translation) and covers.

Both sites already have their own generators; this only drives them the way okadmin's pipeline does:
- scripts/2.generate_ai_guides.py      process_topic(row) → app/content/guide_<slug>.md     (English)
- jpcampus: scripts/3.create_korean_content.py   translate_file(path) → guide_<slug>_kr.md (Korean)
- krcampus: scripts/3.create_japanese_content.py translate_file(path) → guide_<slug>_ja.md (Japanese)
They run with `python3` from PATH inside the site folder, like okadmin's pipeline does.
A cover is drawn with Gemini/Imagen (the okpy venv has google-genai) into
app/static/images/guide_<slug>.jpg and set as the guide's `thumbnail` (English and the translation).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from app import sites
from app import snapshot
from dataclasses import dataclass

COVER_SIZE = (1200, 675)


@dataclass(frozen=True)
class Kit:
    country: str            # where students go to study
    translate_script: str   # English → the second language
    suffix: str             # file suffix of the translation
    lang_ko: str            # 한국어 / 일본어 (for messages)
    lang_en: str            # Korean / Japanese (for prompts)
    categories: tuple[str, ...]   # guide categories used on the site (frontmatter "category"), most used first
    thumb_base: str         # what the site accepts as a guide thumbnail (krcampus only shows absolute URLs)
    places: bool = False    # school/univ photos come from Google Places (the site's scripts/fetch_images.py)
    maps_key: str = "GOOGLE_MAPS_API_KEY"   # the site's .env key for Google Places (guide covers)
    seed_hint: str = ""
    topics_hint: str = ""


KITS = {
    "jpcampus": Kit("Japan", "3.create_korean_content.py", "_kr", "한국어", "Korean",
                    ("Budget", "Culture", "Settlement", "Curated List", "Housing", "Health", "Part-time", "Career",
                     "Visa", "Travel", "Safety", "Life", "Exam", "Selection", "Region", "Cost", "Insurance", "Preparation"),
                    "/static/images/",
                    seed_hint="学生ビザ, 東京 家賃",
                    topics_hint="도쿄에서 학생 아르바이트 구하는 법\n일본 유학 첫 달 생활비"),
    "krcampus": Kit("Korea", "3.create_japanese_content.py", "_ja", "일본어", "Japanese",
                    ("Selection", "Settlement", "Budget", "Region", "Culture", "Curated List", "Visa", "Preparation",
                     "Housing", "Work", "Part-time", "Insurance", "Exam", "Career", "Life", "Cost"),
                    "https://krcampus.net/static/images/", places=True, maps_key="KRCAMPUS_GOOGLE_MAPS_API_KEY",
                    seed_hint="韓国留学, 語学堂",
                    topics_hint="서울 어학당 고르는 법\n한국 유학 첫 달 생활비"),
}
SUFFIXES = tuple(k.suffix for k in KITS.values())


def kit() -> Kit:
    return KITS.get(sites.current_id()) or KITS["jpcampus"]


def __getattr__(name: str):   # jpcontent.CATEGORIES follows the current site
    if name == "CATEGORIES":
        return kit().categories
    raise AttributeError(name)

_WRITE_CODE = r'''
import importlib.util, json, os, sys
sys.path.insert(0, "scripts")
MARK = "__NEXT_RESULT__"
row, translate_script = json.loads(sys.argv[1])
def out(**kw):
    print(MARK + json.dumps(kw, ensure_ascii=False), flush=True)
def load(name, file):
    spec = importlib.util.spec_from_file_location(name, os.path.join("scripts", file))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
g = load("jp_guides", "2.generate_ai_guides.py")
slug = row["slug"]
path = os.path.join(g.OUTPUT_DIR, f"guide_{slug}.md")
if os.path.exists(path):
    out(ok=False, error=f"이미 있는 글이에요 (guide_{slug}.md)"); sys.exit(0)
if g.is_deleted_guide(slug):
    out(ok=False, error="지운 글 목록에 있는 주제예요"); sys.exit(0)
res = g.process_topic(row)
print(res, flush=True)
if not os.path.exists(path):
    out(ok=False, error="글을 만들지 못했어요 (품질 검사를 3번 통과하지 못함)"); sys.exit(0)
kr = ""
if translate_script:
    try:
        k = load("site_translate", translate_script)
        r = k.translate_file(path)
        kr = r.get("file", "") if r.get("status") in ("success", "skipped") else ""
        if r.get("status") == "error":
            print("translation failed: " + str(r.get("msg")), flush=True)
    except Exception as exc:
        print(f"translation failed: {exc}", flush=True)
out(ok=True, path=f"guide_{slug}.md", kr=kr, title=row["title"])
'''

# Runs with a python that has google-genai + Pillow (okpy's venv). cwd = the site folder.
_COVER_CODE = r'''
import base64, json, os, sys
from io import BytesIO
from PIL import Image, ImageOps
MARK = "__NEXT_RESULT__"
target, prompt, size, backup_dir = json.loads(sys.argv[1])
def out(**kw):
    print(MARK + json.dumps(kw, ensure_ascii=False), flush=True)
if not os.getenv("GEMINI_API_KEY"):
    out(ok=False, error="GEMINI_API_KEY missing"); sys.exit(0)
from google import genai
from google.genai import types
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
raw, used, errors = None, "", []
imagen = os.getenv("IMAGEN_MODEL") if os.getenv("NEXT_IMAGEN_OK") else ""
if imagen:
    try:
        resp = client.models.generate_images(model=imagen, prompt=prompt,
            config=types.GenerateImagesConfig(number_of_images=1, aspect_ratio="16:9", output_mime_type="image/png"))
        if resp.generated_images:
            raw, used = resp.generated_images[0].image.image_bytes, imagen
    except Exception as exc:
        errors.append(f"{imagen}: {exc}")
gem = os.getenv("NEXT_GEMINI_IMAGE_MODEL")
if raw is None and gem:
    try:
        try:
            cfg = types.GenerateContentConfig(response_modalities=["IMAGE"], image_config=types.ImageConfig(aspect_ratio="16:9"))
        except Exception:
            cfg = types.GenerateContentConfig(response_modalities=["IMAGE"])
        resp = client.models.generate_content(model=gem, contents=prompt + " Wide 16:9 landscape image.", config=cfg)
        for part in (resp.candidates[0].content.parts if resp.candidates else []):
            if getattr(part, "inline_data", None) and part.inline_data.data:
                raw, used = part.inline_data.data, gem
                break
    except Exception as exc:
        errors.append(f"{gem}: {exc}")
if raw is None:
    out(ok=False, error=("; ".join(errors) or "이미지가 나오지 않았어요")[:400]); sys.exit(0)
if isinstance(raw, str):
    raw = base64.b64decode(raw)
if os.path.isfile(target):
    os.makedirs(backup_dir, exist_ok=True)
    with open(target, "rb") as src, open(os.path.join(backup_dir, os.path.basename(target)), "wb") as dst:
        dst.write(src.read())
img = ImageOps.fit(Image.open(BytesIO(raw)).convert("RGB"), tuple(size))
img.save(target, format="JPEG", quality=85, optimize=True)
out(ok=True, model=used, size=list(size))
'''


def is_site() -> bool:
    return sites.current().content == "campus_guides" and sites.current_id() in KITS


# ---------- posts ----------

def _content_dir() -> Path:
    return Path(snapshot.POSTS_ROOT)


def _front(path: Path) -> tuple[dict[str, Any], str, str]:
    """(meta, raw front block, body) of a campus post (JSON between --- lines)."""
    text = path.read_text(encoding="utf-8")
    m = re.match(r"---\s*\n(.*?)\n---\s*\n", text, re.S)
    if not m:
        return {}, "", text
    try:
        meta = json.loads(m.group(1))
    except json.JSONDecodeError:
        meta = {}
    return (meta if isinstance(meta, dict) else {}), m.group(0), text[m.end():]


def _set_meta(path: Path, **changes: Any) -> bool:
    meta, block, body = _front(path)
    if not block:
        return False
    meta.update(changes)
    path.write_text("---\n" + json.dumps(meta, ensure_ascii=False, indent=2) + "\n---\n" + body, encoding="utf-8")
    return True


def existing_guides() -> dict[str, str]:
    """slug → English title of every guide (so research and writing never repeat one)."""
    out: dict[str, str] = {}
    for path in sorted(_content_dir().glob("guide_*.md")):
        if path.stem.endswith(SUFFIXES):
            continue
        slug = path.stem[len("guide_"):]
        try:
            out[slug] = str(_front(path)[0].get("title") or slug)
        except OSError:
            out[slug] = slug
    return out


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return re.sub(r"-{2,}", "-", s)[:70].strip("-")


def plan_guide(category: str, topic: str) -> dict[str, str]:
    """A topic line (any language) → the row 2.generate_ai_guides.py expects."""
    from app.work import workers
    workers._okadmin_import()
    from llm_claude import claude_json

    have = existing_guides()
    k, site = kit(), sites.current()
    prompt = f"""You plan guides for {site.label} ({site.domain}), a site for international students who want to study in {k.country}.
Turn this request into one English guide topic. Request: {topic}
Category (keep it unless clearly wrong): {category or "pick one"} — one of: {", ".join(k.categories)}
Do not repeat these existing guides: {", ".join(list(have.values())[:150])}
Return ONLY JSON: {{"title": "English title, specific and useful", "slug": "short-kebab-case", "category": "",
"description": "English meta description, 120-155 chars", "prompt": "2-3 sentences: what the guide must cover, for whom, with concrete {k.country}-specific details"}}"""
    data = claude_json(prompt, timeout_sec=180) or {}
    title = str(data.get("title") or topic).strip()
    slug = slugify(str(data.get("slug") or title))
    if not slug:
        raise RuntimeError("주제에서 글 주소(slug)를 만들지 못했어요")
    if slug in have:
        raise RuntimeError(f"이미 있는 글이에요 (guide_{slug}.md)")
    cat = str(data.get("category") or category or "Life").strip()
    return {"slug": slug, "title": title, "category": cat if cat in k.categories else (category or "Life"),
            "description": str(data.get("description") or "").strip()[:200],
            "prompt": str(data.get("prompt") or topic).strip()}


def _site_python() -> str:
    """okadmin's pipeline runs the campus scripts with `python3` from PATH (their venvs lack tqdm)."""
    return shutil.which("python3") or sys.executable


def _image_python() -> str:
    """A python with google-genai: okpy's venv (the campus venvs have none)."""
    for repo in (sites.current().repo, sites.WORK_ROOT / "okpy"):
        for d in (".venv", "venv"):
            py = repo / d / "bin" / "python"
            if py.exists() and list((repo / d / "lib").glob("python*/site-packages/google/genai")):
                return str(py)
    return sys.executable


def cover_prompt(meta: dict[str, Any], hint: str = "") -> str:
    country = kit().country
    base = (f"Warm, bright editorial photo-style illustration for a study-in-{country} guide titled \"{meta.get('title') or ''}\" "
            f"(topic: {meta.get('category') or 'student life'}). International students in {country}, natural light, "
            "no text, no letters, no logos, no watermark, 16:9.")
    return f"{hint.strip()}. {base}" if hint.strip() else base


def draw_cover(work, slug: str, hint: str = "") -> dict[str, Any]:
    """Draw guide_<slug>.jpg and point the English guide's (and its translation's) thumbnail at it."""
    from app.work import workers
    en = _content_dir() / f"guide_{slug}.md"
    if not en.is_file():
        raise RuntimeError("이 이미지를 쓰는 글을 찾지 못했어요")
    models = workers.ensure_image_models()
    if not models.get("imagen") and not models.get("gemini_image"):
        models = workers.ensure_image_models(force=True)
    if not models.get("imagen") and not models.get("gemini_image"):
        raise RuntimeError("이 키로 쓸 수 있는 이미지 모델이 없어요 (Imagen·Gemini 이미지 모두 없음)")
    name = f"guide_{slug}.jpg"
    target = Path(snapshot.IMAGES_ROOT) / name
    result = workers._run_okpy(work, work.role, _COVER_CODE,
                               [str(target), cover_prompt(_front(en)[0], hint), list(COVER_SIZE), str(workers.BACKUP_DIR)],
                               timeout=60 * 5, python=_image_python())
    url = f"{kit().thumb_base}{name}"
    for path in (en, _content_dir() / f"guide_{slug}{kit().suffix}.md"):
        if path.is_file():
            _set_meta(path, thumbnail=url)
    return {**result, "name": name}


def write_guide(work, category: str, topic: str, row: dict[str, Any] | None = None) -> dict[str, Any]:
    """One new guide: plan → English (2.generate_ai_guides) → translation (the site's 3.create_*_content) → cover."""
    from app.work import workers
    if row is None:
        work.say(f"「{topic[:30]}」 주제를 정리하는 중")
        row = plan_guide(category, topic)
    work.say(f"「{row['title'][:30]}」 영어 글을 쓰는 중 (번역까지 10분쯤)")
    result = workers._run_okpy(work, work.role, _WRITE_CODE, [row, kit().translate_script], timeout=60 * 40, python=_site_python())
    try:
        work.say(f"「{row['title'][:30]}」 커버 사진 찾는 중")
        result["cover"] = bool(place_cover(work, row["slug"]).get("ok"))
    except Exception as exc:  # noqa: BLE001 - the guide is written; it keeps the stock photo
        print(f"cover failed: {exc}", file=sys.stderr)
        result["cover"] = False
    return result


# ---------- research ----------

def research_prompt(keywords: list[dict[str, Any]], n_topics: int) -> str:
    kw_lines = "\n".join(f"- {k['word']} ({k['from']}{', ' + str(k['value']) if k.get('value') else ''})" for k in keywords[:24])
    have = existing_guides()
    k, site = kit(), sites.current()
    return f"""You are the editor of {site.label} ({site.domain}): guides for international students who want to study in {k.country}
(costs, visas, housing, part-time work, exams, choosing schools, daily life). Each guide is written in English and translated to {k.lang_en}.
Below are search keywords that are trending or already bring us readers. Recommend {n_topics} NEW guide topics clearly related to them.

Keywords:
{kw_lines}

Do NOT duplicate existing guides ({len(have)}): {", ".join(sorted(have.values())[:220])}

Return ONLY JSON:
{{"topics": [{{"title": "English guide title", "slug": "kebab-case", "category": "one of: {", ".join(k.categories)}",
"description": "English meta description 120-155 chars", "prompt": "2-3 sentences on what to cover",
"keyword": "which keyword above", "why": "한국어 한 줄: 왜 지금 이 주제인가"}}]}}"""


def picks_from(data: dict[str, Any]) -> list[dict[str, Any]]:
    have = existing_guides()
    seen: set[str] = set()
    picks = []
    for i, item in enumerate((data or {}).get("topics") or []):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        slug = slugify(str(item.get("slug") or title))
        if not title or not slug or slug in have or slug in seen:
            continue
        seen.add(slug)
        cat = str(item.get("category") or "").strip()
        row = {"slug": slug, "title": title, "category": cat if cat in kit().categories else "Life",
               "description": str(item.get("description") or "")[:200], "prompt": str(item.get("prompt") or title)}
        picks.append({"id": f"p{int(time.time())}{i}", "bank": "guide", "kind": "가이드", "bank_label": f"가이드 · {row['category']}",
                      "category": row["category"], "row": row, "topic": title,
                      "keyword": str(item.get("keyword") or ""), "why": str(item.get("why") or ""), "status": "new"})
    return picks


# ---------- Google Places photos (krcampus schools · universities) ----------

PLACE_PREFIXES = ("school_", "univ_")

_PLACES_ONE_CODE = r'''
import json, os, shutil, sys
sys.path.insert(0, "scripts")
MARK = "__NEXT_RESULT__"
slug, backup_dir = json.loads(sys.argv[1])
def out(**kw):
    print(MARK + json.dumps(kw, ensure_ascii=False), flush=True)
import fetch_images as f
if not f.API_KEY:
    out(ok=False, error="KRCAMPUS_GOOGLE_MAPS_API_KEY가 없어요 (krcampus/.env)"); sys.exit(0)
item = next((it for it in f.iter_content_items() if it[0] == slug), None)
if item is None:
    out(ok=False, error="이 사진을 쓰는 학교·대학 글을 찾지 못했어요"); sys.exit(0)
path = os.path.join(f.IMAGES_DIR, slug + ".jpg")
if os.path.isfile(path):
    os.makedirs(backup_dir, exist_ok=True)
    shutil.copy2(path, os.path.join(backup_dir, slug + ".jpg"))
res = f.fetch_item(*item, force=True)
if res != "ok":
    out(ok=False, error="Google Places에서 사진을 찾지 못했어요"); sys.exit(0)
from PIL import Image
with Image.open(path) as img:   # same size/quality as scripts/optimize_images.py
    rgb = img.convert("RGB")
    if rgb.width > 800:
        rgb = rgb.resize((800, int(rgb.height * 800 / rgb.width)), Image.Resampling.LANCZOS)
rgb.save(path, "JPEG", quality=75, optimize=True)
out(ok=True, name=item[1])
'''

_PLACES_MISSING_CODE = r'''
import io, json, os, sys, contextlib
sys.path.insert(0, "scripts")
MARK = "__NEXT_RESULT__"
def out(**kw):
    print(MARK + json.dumps(kw, ensure_ascii=False), flush=True)
import fetch_images as f
if not f.API_KEY:
    out(ok=False, error="KRCAMPUS_GOOGLE_MAPS_API_KEY가 없어요 (krcampus/.env)"); sys.exit(0)
before = set(os.listdir(f.IMAGES_DIR))
f.fetch_all_images(only_missing=True)
import optimize_images
optimize_images.optimize_images()
new = sorted(set(os.listdir(f.IMAGES_DIR)) - before)
out(ok=True, added=len(new), names=new[:20])
'''


def has_places() -> bool:
    return is_site() and kit().places


def places_photo(work, name: str) -> dict[str, Any]:
    """Fetch this school's / university's photo again from Google Places (scripts/fetch_images.py)."""
    from app.work import workers
    from app.money import costs
    slug = Path(name).stem
    if not slug.startswith(PLACE_PREFIXES):
        raise RuntimeError("학교·대학 사진(school_…/univ_…)만 Google Places에서 받아요")
    costs.take("places")
    work.say(f"{name} Google Places 사진 받는 중")
    return workers._run_okpy(work, work.role, _PLACES_ONE_CODE, [slug, str(workers.BACKUP_DIR)],
                             timeout=60 * 5, python=_site_python())


def places_missing(work) -> str:
    """Every school/university page without a photo gets one from Google Places, then resized."""
    from app.work import workers
    from app.money import costs
    missing = [p.stem for p in sorted(_content_dir().glob("*.md"))
               if p.stem.startswith(PLACE_PREFIXES) and not p.stem.endswith(SUFFIXES)
               and not (Path(snapshot.IMAGES_ROOT) / f"{p.stem}.jpg").is_file()]
    if not missing:
        return "사진이 빠진 학교·대학이 없어요"
    if len(missing) > costs.left("places"):
        raise RuntimeError(f"사진 없는 학교·대학 {len(missing)}곳 · 오늘 Places 상한이 {costs.left('places')}장 남았어요. "
                           "💰 비용 카드에서 하루 상한을 올리거나 내일 다시 맡겨 주세요")
    costs.take("places", len(missing))
    work.say("사진 없는 학교·대학을 Google Places에서 찾는 중")
    result = workers._run_okpy(work, work.role, _PLACES_MISSING_CODE, [], timeout=60 * 40, python=_site_python())
    n = int(result.get("added") or 0)
    return f"학교·대학 사진 {n}장을 새로 받았어요" if n else "사진이 빠진 학교·대학이 없어요"


# ---------- guide covers: Google Places photo or one of our school photos (no AI, no Unsplash) ----------

COVER_W, COVER_H = 1200, 675

_PLACE_COVER_CODE = r'''
import json, os, sys
from io import BytesIO
MARK = "__NEXT_RESULT__"
query, key_env, target, size, backup_dir = json.loads(sys.argv[1])
def out(**kw):
    print(MARK + json.dumps(kw, ensure_ascii=False), flush=True)
try:
    from dotenv import load_dotenv
    load_dotenv(".env")
except Exception:
    pass
key = os.getenv(key_env) or os.getenv("GOOGLE_MAPS_API_KEY")
if not key:
    out(ok=False, error=f"{key_env}가 없어요 (.env)"); sys.exit(0)
import requests
from PIL import Image, ImageOps
res = requests.post("https://places.googleapis.com/v1/places:searchText", timeout=20,
    headers={"Content-Type": "application/json", "X-Goog-Api-Key": key,
             "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.photos"},
    json={"textQuery": query, "maxResultCount": 5})
if res.status_code != 200:
    out(ok=False, error=f"Places 검색 실패 {res.status_code}: {res.text[:160]}"); sys.exit(0)
place = photo = None
for p in res.json().get("places") or []:
    photos = p.get("photos") or []
    wide = [ph for ph in photos if (ph.get("widthPx") or 0) >= (ph.get("heightPx") or 0) and (ph.get("widthPx") or 0) >= 800]
    if wide or photos:
        place, photo = p, (wide or photos)[0]
        break
if not photo:
    out(ok=False, error="이 장소의 사진을 찾지 못했어요"); sys.exit(0)
img = requests.get(f"https://places.googleapis.com/v1/{photo['name']}/media", params={"maxWidthPx": 1600, "key": key}, timeout=30)
if img.status_code != 200 or not img.headers.get("Content-Type", "").startswith("image"):
    out(ok=False, error=f"사진 받기 실패 {img.status_code}"); sys.exit(0)
if os.path.isfile(target):
    os.makedirs(backup_dir, exist_ok=True)
    with open(target, "rb") as src, open(os.path.join(backup_dir, os.path.basename(target)), "wb") as dst:
        dst.write(src.read())
pic = ImageOps.fit(Image.open(BytesIO(img.content)).convert("RGB"), tuple(size))
pic.save(target, "JPEG", quality=80, optimize=True)
credit = ", ".join(a.get("displayName", "") for a in (photo.get("authorAttributions") or []) if a.get("displayName"))
out(ok=True, place=(place.get("displayName") or {}).get("text", ""), address=place.get("formattedAddress", ""), credit=credit)
'''


def _real_school_photos() -> dict[str, str]:
    """school_/univ_ photos that are real (not the shared placeholder): file name → school name."""
    import hashlib

    images = Path(snapshot.IMAGES_ROOT)
    files = sorted(list(images.glob("school_*.jpg")) + list(images.glob("univ_*.jpg")))
    by_hash: dict[str, list[Path]] = {}
    for f in files:
        try:
            by_hash.setdefault(hashlib.md5(f.read_bytes()).hexdigest(), []).append(f)
        except OSError:
            continue
    out: dict[str, str] = {}
    for group in by_hash.values():
        if len(group) != 1:   # the same file for many schools = a placeholder / fallback
            continue
        f = group[0]
        md = _content_dir() / f"{f.stem}.md"
        title = _front(md)[0].get("title") if md.is_file() else ""
        out[f.name] = str(title or f.stem)
    return out


def pick_cover(meta: dict[str, Any], body: str, hint: str = "") -> dict[str, Any]:
    """Claude (light) decides: a Google Places search for the place this guide is about, or one of our school photos."""
    from app.work import workers
    workers._okadmin_import()
    from llm_claude import MODEL_LIGHT, claude_json

    schools = _real_school_photos()
    k = kit()
    listing = "\n".join(f"- {name}: {title}" for name, title in list(schools.items())[:250]) or "(none)"
    prompt = f"""Pick a real photo for this study-in-{k.country} guide cover. No illustrations, no stock photos.
Guide title: {meta.get('title')}
Category: {meta.get('category')}
Description: {meta.get('description')}
Beginning: {body[:1200]}
{('Owner hint: ' + hint) if hint else ''}

Rules, in this order:
1. If the guide is about a specific place in {k.country} (a city, district, campus area, station, landmark, market, office),
   give a Google Maps search query for that place ("place_query", e.g. "Haeundae Beach Busan", "Shinchon Station Seoul").
2. Else if the guide is about one of OUR schools below, pick its photo file ("school").
3. Else, if a real place clearly stands for the topic (e.g. an immigration office for a visa guide, a well-known
   university campus for a university guide), give that as "place_query".
4. Otherwise both null.
Our school photos:
{listing}
Return ONLY JSON: {{"place_query": "... or null", "school": "file name or null", "why": "한국어 한 줄"}}"""
    data = claude_json(prompt, timeout_sec=90, model=MODEL_LIGHT) or {}
    school = data.get("school") if data.get("school") in schools else None
    query = str(data.get("place_query") or "").strip() or None
    if hint and not query and not school:
        query = hint
    return {"place_query": query, "school": school, "why": str(data.get("why") or "")[:120]}


def place_cover(work, slug: str, hint: str = "") -> dict[str, Any]:
    """guide_<slug>.jpg from Google Places (or a copy of one of our school photos); the guide (and its translation)
    point their thumbnail at it. Nothing changes when neither fits."""
    import shutil

    from app.work import workers
    en = _content_dir() / f"guide_{slug}.md"
    if not en.is_file():
        raise RuntimeError("이 이미지를 쓰는 글을 찾지 못했어요")
    meta, _, body = _front(en)
    choice = pick_cover(meta, body, hint)
    name = f"guide_{slug}.jpg"
    target = Path(snapshot.IMAGES_ROOT) / name
    result: dict[str, Any] = {"ok": False, "name": name, "why": choice["why"]}
    over = ""
    if choice["place_query"]:
        from app.money import costs
        try:
            costs.take("places")
        except costs.OverLimit as exc:
            over, choice["place_query"] = str(exc), None   # today's Places budget is used: a school photo still works
    if choice["place_query"]:
        work.say(f"Google Places에서 「{choice['place_query'][:30]}」 사진 찾는 중")
        try:
            got = workers._run_okpy(work, work.role, _PLACE_COVER_CODE,
                                    [choice["place_query"], kit().maps_key, str(target), [COVER_W, COVER_H], str(workers.BACKUP_DIR)],
                                    timeout=120, python=_site_python())
            result.update(got, source="places", query=choice["place_query"])
        except RuntimeError as exc:
            result["error"] = str(exc)
    if not result.get("ok") and choice["school"]:
        from PIL import Image, ImageOps

        src = Path(snapshot.IMAGES_ROOT) / choice["school"]
        if target.is_file():
            Path(workers.BACKUP_DIR).mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, Path(workers.BACKUP_DIR) / name)
        with Image.open(src) as im:
            ImageOps.fit(im.convert("RGB"), (COVER_W, COVER_H)).save(target, "JPEG", quality=80, optimize=True)
        result.update(ok=True, source="school", school=choice["school"])
    if not result.get("ok"):
        if over:
            result["error"], result["over"] = over, True
        return result
    url = f"{kit().thumb_base}{name}"
    credit = result.get("credit") or ""
    for path in (en, _content_dir() / f"guide_{slug}{kit().suffix}.md"):
        if path.is_file():
            changes: dict[str, Any] = {"thumbnail": url}
            if result.get("source") == "places":
                changes["thumbnail_credit"] = f"Photo: {credit} / Google" if credit else "Photo: Google"
            _set_meta(path, **changes)
    return result


def stock_cover_slugs() -> list[str]:
    """Guides still on a stock (Unsplash) or no thumbnail."""
    out = []
    for path in sorted(_content_dir().glob("guide_*.md")):
        if path.stem.endswith(SUFFIXES):
            continue
        thumb = str(_front(path)[0].get("thumbnail") or "")
        if not thumb or "unsplash.com" in thumb:
            out.append(path.stem[len("guide_"):])
    return out


def replace_stock_covers(work, limit: int = 40) -> str:
    """Move guides off Unsplash: a Places photo or one of our school photos for each (up to `limit` per run)."""
    todo = stock_cover_slugs()
    if not todo:
        return "Unsplash 커버를 쓰는 가이드가 없어요"
    done = skipped = failed = 0
    stopped = ""
    for n, slug in enumerate(todo[:limit], 1):
        work.say(f"{n}/{min(len(todo), limit)} 가이드 커버 바꾸는 중")
        try:
            r = place_cover(work, slug)
            if r.get("ok"):
                done += 1
            elif r.get("over"):
                stopped = r["error"]
                break
            else:
                skipped += 1
        except Exception as exc:  # noqa: BLE001
            print(f"cover {slug}: {exc}", file=sys.stderr)
            failed += 1
    left = len(stock_cover_slugs())
    return (f"가이드 커버 {done}개를 실제 사진으로 바꿨어요" + (f" · 맞는 사진이 없어 그대로 {skipped}개" if skipped else "")
            + (f" · 실패 {failed}개" if failed else "") + (f" · {stopped}" if stopped else "")
            + (f" · 남은 {left}개는 다음에 이어서" if left and not stopped else ""))
