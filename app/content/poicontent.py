"""Playground place sites (2층): OK Ramen · OK Caddie · OK Onsen · JP Fun — place pages + guides, photos from Google Places (never drawn).

Each site keeps its content as Markdown per language (English + Korean, OK Caddie also Japanese):
- places  app/content/<place>_en.md · _ko.md (· _ja.md)   ← the site's own generator (one CSV row per place)
          okramen script/ramen_generator.run_generator · okcaddie script/course_generator.process_courses
          · okonsen script/onsen_generator.process_csv_auto · jpfun script/item_generator.run_batch
- guides  app/content/guides/<id>_en.md …                 ← script/guide_generator.py (one CSV row: id, topic_en, topic_ko, keywords)
- photos  app/static/images/<place>.jpg                   ← Google Places photo of that place
New places are first looked up with Google Places Text Search here, so coordinates and address come from the
map, not from the model. Scripts run with `python3` from PATH inside the site folder (like okadmin's pipeline);
queue CSVs are temp files passed by TOPIC_QUEUE_* env. After writing, script/build_data.py rebuilds the map data.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app import sites
from app import snapshot
@dataclass(frozen=True)
class Kit:
    noun_en: str                    # "ramen shop" / "golf course"
    noun_ko: str                    # 가게 / 골프장
    section: str                    # snapshot section key (shop / course)
    item_module: str                # the site's generator module in script/
    item_entry: str                 # its function, called with limit=1
    csv_header: tuple[str, ...]     # columns the generator reads
    place_types: tuple[str, ...]    # Places types that count as this kind of place
    search_word: str                # added to the Places text search
    check: str                      # content_quality function that rejects off-theme rows
    domain: str                     # one line for prompts
    features_hint: str
    langs: tuple[str, ...] = ("en", "ko")
    name_rules: tuple[tuple[str, str], ...] = ((" ", "_"), ("'", ""), (",", ""))
    guide_call: str = "main"        # guide_generator: main(["--new-topics", "1"]) or generate_guides_parallel(limit=1)
    real_data_check: bool = True    # content_quality.has_real_shop_data exists
    seed_hint: str = ""             # write UI: seed_extra placeholder
    topics_hint: str = ""           # write UI: topics textarea placeholder


KITS = {
    "okramen": Kit("ramen shop", "가게", "shop", "ramen_generator", "run_generator",
                   ("Name", "Lat", "Lng", "Address", "Thumbnail", "Features"),
                   ("ramen_restaurant", "japanese_restaurant", "restaurant", "meal_takeaway", "food"), "ramen", "is_non_ramen_slug",
                   "OK Ramen (okramen.net): ramen shops in Japan for travelers, English + Korean pages",
                   "flavor (돈코츠/tonkotsu, 쇼유/shoyu, 시오/shio, 미소/miso, 츠케멘/tsukemen, 치킨/chicken) + vibe (현지인맛집, 심야, 혼밥, 프리미엄)",
                   seed_hint="ramen tokyo, 二郎系, tsukemen",
                   topics_hint="이치란 신주쿠 (가게)\n도쿄 심야 라멘 가이드 (가이드)"),
    "okcaddie": Kit("golf course", "골프장", "course", "course_generator", "process_courses",
                    ("Name", "Lat", "Lng", "Address", "Features", "Booking"),
                    ("golf_course", "sports_club", "sports_complex", "resort_hotel"), "golf course", "is_non_golf_course_slug",
                    "OK Caddie (okcaddie.net): golf courses in Japan for visiting golfers, English + Korean + Japanese pages",
                    "Public, Private Club, Resort, Premium / Luxury, Historic, Tournament, Links, Mountain, Seaside, Beginner Friendly",
                    langs=("en", "ko", "ja"), name_rules=((" ", "_"), ("'", ""), (",", ""), ("&", "and"), (".", "")),
                    guide_call="generate_guides_parallel", real_data_check=False,
                    seed_hint="golf japan, ゴルフ場 予約, 北海道 ゴルフ",
                    topics_hint="히로노 골프 클럽\n홋카이도 여름 골프 여행 (가이드)"),
    "okonsen": Kit("onsen ryokan", "온천", "onsen", "onsen_generator", "process_csv_auto",
                   ("Name", "Lat", "Lng", "Address", "Features"),
                   ("japanese_inn", "spa", "public_bath", "lodging", "resort_hotel", "hotel", "tourist_attraction"), "onsen", "is_off_onsen_theme",
                   "OK Onsen (okonsen.net): onsen and ryokan in Japan for travelers, English + Korean pages",
                   "Private Bath / 가족탕, Tattoo OK / 타투 허용, Great View / 절경, Luxury / 고급 료칸, Local / 로컬",
                   guide_call="run_batch", real_data_check=False,
                   seed_hint="onsen japan, 温泉 旅館, 貸切風呂",
                   topics_hint="하코네 유모토 온천 료칸\n타투 OK 온천 고르는 법 (가이드)"),
    "jpfun": Kit("outdoor leisure spot (ski resort, surf point, dive site or campground)", "레저 스팟", "spot",
                 "item_generator", "run_batch",
                 ("Name", "Name_KO", "Id", "Activity", "Lat", "Lng", "Address", "Features", "Website", "Region"),
                 ("ski_resort", "campground", "camping_cabin", "rv_park", "beach", "marina", "dive_center", "scuba_diving",
                  "surf_shop", "sports_activity_location", "adventure_sports_center", "park", "national_park", "state_park",
                  "tourist_attraction", "lodging", "resort_hotel", "natural_feature"), "", "",
                 "JP Fun (jpfun.net): ski, surf, dive and camp spots in Japan on one map, English + Korean pages",
                 "the FIRST tag must be the activity: ski / surf / dive / camp; then traits like Powder, Beginner, Family, Reef, Glamping",
                 name_rules=((" ", "_"), ("'", ""), (",", ""), ("&", ""), (".", ""), ("-", ""), ("(", ""), (")", ""), ("/", ""),
                             (":", ""), ("!", "")),
                 guide_call="run_batch", real_data_check=False,
                 seed_hint="ski japan, surf japan, 沖縄 ダイビング, キャンプ場",
                 topics_hint="하쿠바 핫포네 스키장\n오키나와 첫 다이빙 준비 (가이드)"),
}
LANG_SUFFIXES = ("_en", "_ko", "_ja")
GUIDES_DIR = "guides"


def __getattr__(name: str):   # poicontent.CATEGORIES follows the current site: (가게|골프장, 가이드)
    if name == "CATEGORIES":
        return (kit().noun_ko, "가이드")
    raise AttributeError(name)


def is_site() -> bool:
    return sites.current().content == "poi"


def kit() -> Kit:
    return KITS.get(sites.current().id) or KITS["okramen"]


def _content_dir() -> Path:
    return Path(snapshot.POSTS_ROOT)


def base_of(stem: str) -> str:
    """afuri_harajuku_ko → afuri_harajuku"""
    for suf in LANG_SUFFIXES:
        if stem.endswith(suf):
            return stem[: -len(suf)]
    return stem


def safe_name(name: str) -> str:
    """Same rule as the site's generator (file names must match)."""
    out = name.strip().lower()
    for a, b in kit().name_rules:
        out = out.replace(a, b)
    return out


def existing_shops() -> dict[str, str]:
    """safe name → shop name (from the English page title line)."""
    out: dict[str, str] = {}
    root = _content_dir()
    if root.is_dir():
        for p in root.glob("*.md"):
            out.setdefault(base_of(p.stem), base_of(p.stem).replace("_", " "))
    return out


def existing_guides() -> dict[str, str]:
    out: dict[str, str] = {}
    root = _content_dir() / GUIDES_DIR
    if root.is_dir():
        for p in root.glob("*.md"):
            out.setdefault(base_of(p.stem), base_of(p.stem).replace("_", " "))
    return out


def _python() -> str:
    from app.content import jpcontent
    return jpcontent._site_python()


# ---------- write: one shop ----------

_ITEM_CODE = r'''
import csv, json, os, subprocess, sys, tempfile
sys.path.insert(0, "script")
MARK = "__NEXT_RESULT__"
row, k = json.loads(sys.argv[1])
def out(**kw):
    print(MARK + json.dumps(kw, ensure_ascii=False), flush=True)
from dotenv import load_dotenv
load_dotenv()
import requests
key = os.getenv("GOOGLE_PLACES_API_KEY") or os.getenv("GOOGLE_MAPS_API_KEY")
if not key:
    out(ok=False, error="GOOGLE_PLACES_API_KEY가 없어요"); sys.exit(0)
q = " ".join(x for x in (row.get("name"), row.get("area"), k["search_word"], "Japan") if x)
res = requests.post("https://places.googleapis.com/v1/places:searchText",
    headers={"Content-Type": "application/json", "X-Goog-Api-Key": key,
             "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.location,places.photos,places.types,places.businessStatus"},
    json={"textQuery": q, "languageCode": "en", "regionCode": "JP", "maxResultCount": 5}, timeout=20).json()
if res.get("error"):
    m = str(res["error"].get("message") or "")
    out(ok=False, error=("Google Places 키가 만료됐거나 잘못됐어요 (Secret Manager의 키를 확인해 주세요)"
                         if ("expired" in m.lower() or "invalid" in m.lower()) else f"Google Places 오류: {m[:150]}")); sys.exit(0)
places = [p for p in res.get("places") or [] if set(p.get("types") or []) & set(k["place_types"])]
if not places:
    out(ok=False, error=f"Google 지도에서 「{row.get('name')}」을(를) 찾지 못했어요"); sys.exit(0)
p = places[0]
if p.get("businessStatus") == "CLOSED_PERMANENTLY":
    out(ok=False, error=f"「{p['displayName']['text']}」은(는) 문을 닫은 곳이에요"); sys.exit(0)
name = (row.get("name") or p["displayName"]["text"]).strip()
loc = p.get("location") or {}
lat, lng, addr = loc.get("latitude"), loc.get("longitude"), (p.get("formattedAddress") or "").replace(", Japan", "").strip()
safe = name.lower()
for a, b in k["name_rules"]:
    safe = safe.replace(a, b)
cq = None
if k["check"] or k["real_data_check"]:
    import content_quality as cq
if k["check"] and getattr(cq, k["check"])(safe, name, features=row.get("features", ""), address=addr):
    out(ok=False, error=f"「{name}」은(는) 이 사이트에 맞는 곳({k['noun_ko']})이 아니라서 건너뛰어요"); sys.exit(0)
if k["real_data_check"] and not cq.has_real_shop_data(lat=lat, lng=lng, address=addr):
    out(ok=False, error="지도 좌표·주소가 확실하지 않아서 건너뛰어요"); sys.exit(0)
content = os.path.join("app", "content")
pages = {lang: os.path.join(content, f"{safe}_{lang}.md") for lang in k["langs"]}
if any(os.path.exists(x) for x in pages.values()):
    out(ok=False, error=f"이미 있는 {k['noun_ko']}이에요 ({safe})"); sys.exit(0)
feats = row.get("features", "")
act = next((a for a in ("ski", "surf", "dive", "camp") if a in feats.lower()), "")
vals = {"Name": name, "Lat": lat, "Lng": lng, "Address": addr, "Features": feats, "Id": safe, "Activity": act,
        "Name_KO": row.get("name_ko", ""), "Region": "", "Website": ""}
tmp = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8", newline="")
w = csv.writer(tmp)
w.writerow(k["csv_header"])
w.writerow([vals.get(c, "") for c in k["csv_header"]])
tmp.close()
os.environ["TOPIC_QUEUE_ITEMS"] = tmp.name
print(f"queue: {name} · {lat},{lng} · {addr}", flush=True)
import importlib
gen = importlib.import_module(k["item_module"])
getattr(gen, k["item_entry"])(1)
os.unlink(tmp.name)
en = pages["en"]
if not os.path.exists(en):
    out(ok=False, error="페이지를 만들지 못했어요 (품질 검사를 통과하지 못함)"); sys.exit(0)
photo = False
img = os.path.join("app", "static", "images", safe + ".jpg")
ph = (p.get("photos") or [{}])[0].get("name")
if ph and not os.path.exists(img):
    r = requests.get(f"https://places.googleapis.com/v1/{ph}/media", params={"maxWidthPx": 800, "key": key}, timeout=20)
    if r.status_code == 200 and r.headers.get("Content-Type", "").startswith("image"):
        with open(img, "wb") as fh:
            fh.write(r.content)
        try:
            from PIL import Image
            with Image.open(img) as im:
                rgb = im.convert("RGB")
            if rgb.width > 800:
                rgb = rgb.resize((800, int(rgb.height * 800 / rgb.width)), Image.Resampling.LANCZOS)
            rgb.save(img, "JPEG", quality=78, optimize=True)
        except Exception as exc:
            print(f"resize skipped: {exc}", flush=True)
        photo = True
b = subprocess.run([sys.executable, "script/build_data.py"], capture_output=True, text=True)
print((b.stdout or "")[-400:], (b.stderr or "")[-400:], flush=True)
title = name
for line in open(en, encoding="utf-8").read().splitlines()[:30]:
    if line.startswith("title:"):
        title = line.split(":", 1)[1].strip().strip("'\""); break
out(ok=True, path=safe + "_en.md", langs=[l for l, x in pages.items() if os.path.exists(x)], title=title, photo=photo, address=addr)
'''

_GUIDE_CODE = r'''
import csv, json, os, subprocess, sys, tempfile
sys.path.insert(0, "script")
MARK = "__NEXT_RESULT__"
row, call, langs = json.loads(sys.argv[1])
def out(**kw):
    print(MARK + json.dumps(kw, ensure_ascii=False), flush=True)
gid = row["id"]
gdir = os.path.join("app", "content", "guides")
en = os.path.join(gdir, gid + "_en.md")
if os.path.exists(en):
    out(ok=False, error=f"이미 있는 가이드예요 ({gid})"); sys.exit(0)
tmp = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8", newline="")
w = csv.writer(tmp)
w.writerow(["id", "topic_en", "topic_ko", "keywords"])
w.writerow([gid, row["topic_en"], row["topic_ko"], row.get("keywords", "")])
tmp.close()
os.environ["TOPIC_QUEUE_GUIDES"] = tmp.name
import guide_generator as g
if call == "main":
    g.main(["--new-topics", "1"])
else:
    getattr(g, call)(limit=1)
os.unlink(tmp.name)
if not os.path.exists(en):
    out(ok=False, error="가이드를 만들지 못했어요 (품질 검사를 통과하지 못함)"); sys.exit(0)
b = subprocess.run([sys.executable, "script/build_data.py"], capture_output=True, text=True)
print((b.stdout or "")[-300:], flush=True)
title = row["topic_en"]
for line in open(en, encoding="utf-8").read().splitlines()[:20]:
    if line.startswith("title:"):
        title = line.split(":", 1)[1].strip().strip("'\""); break
out(ok=True, path="guides/" + gid + "_en.md", langs=[l for l in langs if os.path.exists(os.path.join(gdir, f"{gid}_{l}.md"))], title=title)
'''


LANG_KO = {"en": "영어", "ko": "한국어", "ja": "일본어"}


def langs_text() -> str:
    return "·".join(LANG_KO[x] for x in kit().langs)


def write_shop(work, row: dict[str, Any]) -> dict[str, Any]:
    """One place's pages (English + translations) from a real map place, plus its Places photo."""
    from app.money import costs
    from app.work import workers
    costs.take("places", 2)   # 💰 text search + photo
    k = kit()
    work.say(f"「{row.get('name')}」 Google 지도에서 찾는 중 → {langs_text()} 페이지 쓰기 (10분쯤)")
    spec = {"place_types": list(k.place_types), "search_word": k.search_word, "check": k.check, "noun_ko": k.noun_ko,
            "langs": list(k.langs), "name_rules": [list(r) for r in k.name_rules], "csv_header": list(k.csv_header),
            "item_module": k.item_module, "item_entry": k.item_entry, "real_data_check": k.real_data_check}
    result = workers._run_okpy(work, work.role, _ITEM_CODE, [row, spec], timeout=60 * 40, python=_python())
    snapshot.refresh_snapshot()
    return result


def write_guide(work, row: dict[str, Any]) -> dict[str, Any]:
    from app.work import workers
    work.say(f"「{row['topic_ko'][:30]}」 가이드 {langs_text()}로 쓰는 중 (10분쯤)")
    result = workers._run_okpy(work, work.role, _GUIDE_CODE, [row, kit().guide_call, list(kit().langs)], timeout=60 * 40, python=_python())
    snapshot.refresh_snapshot()
    return result


def plan(category: str, topic: str) -> dict[str, Any]:
    """'이치란 신주쿠' / '도쿄 심야 라멘 가이드' → a row the generators take (Claude, light)."""
    from app.work import workers
    workers._okadmin_import()
    from llm_claude import MODEL_LIGHT, claude_json

    k = kit()
    if category != "가이드":
        data = claude_json(
            f"For {k.domain}. Identify this real {k.noun_en} in Japan: {topic}\n"
            'Return ONLY JSON: {"name": "official English name (with branch for chains)", '
            f'"area": "city / neighborhood", "features": "comma tags: {k.features_hint}"}} — use null for name if unsure it exists.',
            timeout_sec=90, model=MODEL_LIGHT) or {}
        if not data.get("name"):
            raise RuntimeError(f"「{topic[:30]}」 {k.noun_ko}을(를) 특정하지 못했어요. 이름과 지역을 같이 적어 주세요")
        return {"name": str(data["name"]), "area": str(data.get("area") or ""), "features": str(data.get("features") or "")}
    data = claude_json(
        f"For {k.domain}. Make a guide row for this topic: {topic}\n"
        'Return ONLY JSON: {"id": "short-id", "topic_en": "English guide topic", "topic_ko": "한국어 주제", "keywords": "english search keywords"}',
        timeout_sec=90, model=MODEL_LIGHT) or {}
    gid = guide_id(str(data.get("id") or ""))
    if not gid or not data.get("topic_en"):
        raise RuntimeError("가이드 주제를 정리하지 못했어요")
    if gid in existing_guides():
        raise RuntimeError(f"이미 있는 가이드예요 ({gid})")
    return {"id": gid, "topic_en": str(data["topic_en"]), "topic_ko": str(data.get("topic_ko") or topic), "keywords": str(data.get("keywords") or "")}


def guide_id(raw: str) -> str:
    """okramen ids use _, okcaddie ids use - (kept as the site writes them)."""
    sep = "-" if any("-" in g for g in existing_guides()) and "_" not in raw else "_"
    return re.sub(r"[^a-z0-9]+", sep, raw.lower()).strip(sep)


def write(work, category: str, topic: str, row: dict[str, Any] | None = None) -> dict[str, Any]:
    category = "가이드" if category in ("가이드", "guide") else kit().noun_ko
    row = row or plan(category, topic)
    return write_guide(work, row) if category == "가이드" else write_shop(work, row)


# ---------- research ----------

def research_prompt(keywords: list[dict[str, Any]], n: int) -> str:
    k = kit()
    kw_lines = "\n".join(f"- {x['word']} ({x['from']})" for x in keywords[:24])
    shops = sorted(existing_shops().values())
    guides = sorted(existing_guides().values())
    n_guides = max(1, n // 4)
    return f"""You are the editor of {k.domain}.
Using the search keywords below, recommend {n - n_guides} {k.noun_en}s to add and {n_guides} new guide topics.
Places MUST be real and currently open, ones visitors search for (well-known or rising), each with its area.

Keywords:
{kw_lines}

Already covered {k.noun_en}s ({len(shops)}), do NOT repeat: {", ".join(shops[:300])}
Already covered guides ({len(guides)}): {", ".join(guides[:200])}

Return ONLY JSON:
{{"shops": [{{"name": "official English name", "area": "city / prefecture", "features": "comma tags: {k.features_hint}",
"keyword": "which keyword above", "why": "한국어 한 줄: 왜 지금 이 {k.noun_ko}인가"}}],
"guides": [{{"id": "short-id", "topic_en": "English topic", "topic_ko": "한국어 주제", "keywords": "english search keywords",
"keyword": "which keyword above", "why": "한국어 한 줄"}}]}}"""


def picks_from(data: dict[str, Any]) -> list[dict[str, Any]]:
    have_s, have_g = set(existing_shops()), set(existing_guides())
    out: list[dict[str, Any]] = []
    stamp = int(time.time())
    for i, s in enumerate((data or {}).get("shops") or []):
        if not isinstance(s, dict) or not s.get("name"):
            continue
        sn = safe_name(str(s["name"]))
        if sn in have_s or any(p["row"].get("name") == s["name"] for p in out):
            continue
        row = {"name": str(s["name"]), "area": str(s.get("area") or ""), "features": str(s.get("features") or "")}
        noun = kit().noun_ko
        out.append({"id": f"p{stamp}s{i}", "bank": "shop", "kind": noun, "bank_label": f"{noun} · {row['area']}".rstrip(" ·"),
                    "category": noun, "row": row, "topic": f"{row['name']}" + (f" ({row['area']})" if row["area"] else ""),
                    "keyword": str(s.get("keyword") or ""), "why": str(s.get("why") or ""), "status": "new"})
    for i, g in enumerate((data or {}).get("guides") or []):
        if not isinstance(g, dict):
            continue
        gid = guide_id(str(g.get("id") or ""))
        if not gid or gid in have_g or not g.get("topic_en"):
            continue
        row = {"id": gid, "topic_en": str(g["topic_en"]), "topic_ko": str(g.get("topic_ko") or g["topic_en"]),
               "keywords": str(g.get("keywords") or "")}
        out.append({"id": f"p{stamp}g{i}", "bank": "guide", "kind": "가이드", "bank_label": "가이드",
                    "category": "가이드", "row": row, "topic": row["topic_ko"],
                    "keyword": str(g.get("keyword") or ""), "why": str(g.get("why") or ""), "status": "new"})
    return out


# ---------- photos: Google Places only ----------

_PHOTO_CODE = r'''
import json, os, shutil, sys
sys.path.insert(0, "script")
MARK = "__NEXT_RESULT__"
names, backup_dir, force = json.loads(sys.argv[1])
def out(**kw):
    print(MARK + json.dumps(kw, ensure_ascii=False), flush=True)
import inspect
import fetch_images as f
key = os.getenv("GOOGLE_PLACES_API_KEY") or getattr(f, "API_KEY", "")
if not key:
    out(ok=False, error="GOOGLE_PLACES_API_KEY가 없어요"); sys.exit(0)
def call(fn, *a):   # okramen: fn(..), okcaddie: fn(.., api_key)
    return fn(*a, key) if "api_key" in inspect.signature(fn).parameters else fn(*a)
done, missed = [], []
for safe in names:
    md = os.path.join("app", "content", safe + "_en.md")
    if not os.path.exists(md):
        md = os.path.join("app", "content", safe + "_ko.md")
    meta = {}
    for line in open(md, encoding="utf-8").read().split("---")[1].splitlines():
        if ":" in line and not line.startswith(" "):
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip().strip("'\"")
    name = meta.get("shop_name") or meta.get("name") or safe.replace("_", " ").title()
    lat, lng = meta.get("lat"), meta.get("lng")
    if not lat or not lng:
        missed.append(safe); continue
    if hasattr(f, "search_place"):
        place = call(f.search_place, name, lat, lng)
    else:   # jpfun
        place = f.pick_place_with_photo(f.search_places(name, float(lat), float(lng)), name)
    photos = (place or {}).get("photos") or []
    if not photos:
        missed.append(safe); continue
    path = os.path.join(f.IMAGES_DIR, safe + ".jpg")
    if os.path.isfile(path):
        if not force:
            continue
        os.makedirs(backup_dir, exist_ok=True)
        shutil.copy2(path, os.path.join(backup_dir, safe + ".jpg"))
    if (call(f.download_photo, photos[0].get("name", ""), path) if hasattr(f, "download_photo")
            else f.download_places_photo(photos[0].get("name", ""), __import__("pathlib").Path(path))):
        try:
            from PIL import Image
            with Image.open(path) as im:
                rgb = im.convert("RGB")
            if rgb.width > 800:
                rgb = rgb.resize((800, int(rgb.height * 800 / rgb.width)), Image.Resampling.LANCZOS)
            rgb.save(path, "JPEG", quality=78, optimize=True)
        except Exception as exc:
            print(f"resize skipped: {exc}", flush=True)
        done.append(safe)
    else:
        missed.append(safe)
out(ok=True, done=done, missed=missed)
'''


def shops_without_photo() -> list[str]:
    images = Path(snapshot.IMAGES_ROOT)
    return sorted(s for s in existing_shops() if not (images / f"{s}.jpg").is_file())


def places_photo(work, name: str) -> dict[str, Any]:
    """Fetch this shop's photo again from Google Places (search by its map coordinates)."""
    from app.money import costs
    from app.work import workers
    safe = Path(name).stem
    if safe not in existing_shops():
        raise RuntimeError(f"{kit().noun_ko} 사진({kit().noun_ko} 페이지가 있는 이미지)만 Google Places에서 다시 받아요")
    costs.take("places", 2)
    work.say(f"{name} Google Places 사진 받는 중")
    res = workers._run_okpy(work, work.role, _PHOTO_CODE, [[safe], str(workers.BACKUP_DIR), True], timeout=60 * 5, python=_python())
    if not res.get("done"):
        raise RuntimeError(f"Google Places에서 이 {kit().noun_ko} 사진을 찾지 못했어요")
    res["gcs"] = workers._gcs_push([f"{s}.jpg" for s in res.get("done") or []])
    return res


def places_missing(work) -> str:
    from app.money import costs
    from app.work import workers
    missing = shops_without_photo()
    if not missing:
        return f"사진이 빠진 {kit().noun_ko}이 없어요"
    if 2 * len(missing) > costs.left("places"):
        raise RuntimeError(f"사진 없는 {kit().noun_ko} {len(missing)}곳 · 오늘 Places 상한이 {costs.left('places')}건 남았어요. "
                           "💰 비용 카드에서 하루 상한을 올리거나 내일 다시 맡겨 주세요")
    costs.take("places", 2 * len(missing))
    work.say(f"사진 없는 {kit().noun_ko} {len(missing)}곳 Google Places에서 받는 중")
    res = workers._run_okpy(work, work.role, _PHOTO_CODE, [missing, str(workers.BACKUP_DIR), False], timeout=60 * 30, python=_python())
    n, miss = len(res.get("done") or []), len(res.get("missed") or [])
    note = workers._gcs_push([f"{s}.jpg" for s in res.get("done") or []])
    return (f"{kit().noun_ko} 사진 {n}장을 받았어요" + (f" · {miss}곳은 Places에 사진이 없어요" if miss else "")
            + (f" · {note}" if note else ""))
