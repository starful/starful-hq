"""What each person actually does when you hand them work. AI does the work.

Content kits (sites.current().content → module):
  okpy_posts     → okpy/scripts/generate_posts.py
  campus_guides  → jpcontent.py (jpcampus · krcampus)
  companydb      → cdbcontent.py
  poi            → poicontent.py (okramen · okcaddie · okonsen · jpfun)

Shared tools:
- okadmin/gsc_seo_worker.py        (SEO title/description)  → 민재
- Imagen / Places covers                                    → 하늘
- okadmin/github_ops.py            (issue/PR/review/merge)  → 유나 · 다은 · 소이
- site deploy.sh                                            → 도윤
"""

from __future__ import annotations
from app.paths import HQ_ROOT

import json
import re
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from app.content import contentkit
from app import office
from app import sites
from app import snapshot
from app import tools
ROOT = HQ_ROOT  # starful-hq/ (data lives next to app/)
LOG_DIR = ROOT / "data" / "logs"
BACKUP_DIR = ROOT / "data" / "image_backups"
OKPY = snapshot.OKPY_ROOT
MARK = "__NEXT_RESULT__"
CATEGORIES = contentkit.OKPY_CATEGORIES


# ---------- helpers ----------

def _okadmin_import() -> None:
    path = str(snapshot.OKADMIN_ROOT)
    if path not in sys.path:
        sys.path.insert(0, path)
    os.environ.setdefault("WORK_ROOT", str(snapshot.WORK_ROOT))


def topic_already_covered(category: str, topic: str) -> bool:
    """True when this site already has a post for the topic (okpy stem match; others: False)."""
    if not topic or sites.current().content != "okpy_posts":
        return False
    try:
        sys.path.insert(0, str(OKPY / "scripts"))
        import generate_posts as g  # noqa: WPS433

        return bool(g._topic_already_covered(category, topic))
    except Exception:  # noqa: BLE001
        return False


def categories() -> tuple[str, ...]:
    """Post categories of the current site (the 직접 주제 form and meeting 'write' actions)."""
    return contentkit.categories()


def _need_content() -> None:
    """Writing / covers need a connected content kit (okpy_posts · campus_guides · companydb · poi)."""
    site = sites.current()
    if not site.can_write:
        raise RuntimeError(f"{site.label}는 아직 글쓰기·이미지가 연결되지 않았어요 (검색·분석·반영은 돼요)")


def _okpy_python() -> str:
    venv = next((OKPY / d / "bin" / "python" for d in (".venv", "venv") if (OKPY / d / "bin" / "python").exists()), OKPY / ".venv" / "bin" / "python")
    return str(venv) if venv.exists() else sys.executable


def _ko(err: Any) -> str:
    """Turn common tool errors into one plain Korean line."""
    text = str(err or "").strip() or "알 수 없는 오류"
    low = text.lower()
    first = text.splitlines()[0].strip() if text else ""
    if "gh not logged in" in low or "gh auth login" in low:
        return "GitHub 로그인이 필요해요. 터미널에서 gh auth login"
    if "gh cli not found" in low:
        return "gh 명령이 없어요. brew install gh"
    if "session limit" in low or "usage limit" in low or "rate limit" in low:
        return "Claude 사용 한도에 걸렸어요. 잠시 뒤에 다시 맡겨 주세요"
    if "not logged in" in low and "claude" in low:
        return "Claude CLI 로그인이 필요해요"
    if "gemini_api_key" in low:
        return "이미지용 GEMINI_API_KEY가 없어요 (okpy/.env)"
    if "returned no json" in low or "no json" in low:
        return "Claude가 JSON을 주지 않았어요. 다시 맡겨 주세요"
    # Model sometimes fails with a fenced dump; first line is literally ```json
    if first.startswith("```") or first in ("json", "{", "["):
        return "Claude 응답을 읽지 못했어요. 다시 맡겨 주세요"
    if "on main" in low and "nothing to merge" in low:
        return "이미 main이에요 · 열린 PR이 없어 리뷰·머지할 게 없어요"
    if "no open pr" in low or "no pull requests" in low:
        return "열린 PR이 없어요"
    return first[:200]


def _okpy_env() -> dict[str, str]:
    """Same env okadmin's pipeline gives okpy scripts (topic queue CSV paths, limits)."""
    env = {**os.environ}
    try:   # Places / Maps keys from GCP Secret Manager (like okadmin); site .env files may hold old ones
        from app import gsecrets
        env.update(gsecrets.script_env())
    except Exception as exc:  # noqa: BLE001 - the site's own .env still works
        print(f"secret keys unavailable: {exc}", file=sys.stderr)
    try:
        _okadmin_import()
        from content_pipeline import pipeline_env_for_site

        env.update({k: str(v) for k, v in (pipeline_env_for_site(sites.current().oid) or {}).items()})
    except Exception as exc:  # noqa: BLE001 - still run; the script falls back to okpy/data CSVs
        print(f"pipeline env unavailable: {exc}", file=sys.stderr)
    models = _read_json(IMAGE_MODELS_PATH)
    if models.get("imagen"):
        env["IMAGEN_MODEL"] = models["imagen"]  # generate_posts covers use this
        env["NEXT_IMAGEN_OK"] = "1"
    if models.get("gemini_image"):
        env["NEXT_GEMINI_IMAGE_MODEL"] = models["gemini_image"]
    return env


# ---------- image models ----------
# Google retires Imagen model names (imagen-4.0-fast-generate-001 → 404). Ask the API which image
# models this key can use and pass the working one to okpy scripts. Cached for half a day.

IMAGE_MODELS_PATH = ROOT / "data" / "image_models.json"
IMAGE_MODELS_TTL = 12 * 3600
IMAGEN_PREFER = ("imagen-4.0-fast-generate-001", "imagen-4.0-generate-001", "imagen-4.0-ultra-generate-001",
                 "imagen-4.0-generate-preview-06-06", "imagen-3.0-generate-002", "imagen-3.0-fast-generate-001")
GEMINI_IMAGE_PREFER = ("gemini-3.1-flash-image", "gemini-2.5-flash-image", "gemini-2.5-flash-image-preview", "gemini-3-pro-image-preview",
                       "gemini-2.0-flash-preview-image-generation")

_MODELS_CODE = r'''
import json, os, sys
MARK = "__NEXT_RESULT__"
def out(**kw):
    print(MARK + json.dumps(kw, ensure_ascii=False), flush=True)
if not os.getenv("GEMINI_API_KEY"):
    out(ok=False, error="GEMINI_API_KEY missing"); sys.exit(0)
from google import genai
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
rows = []
for m in client.models.list():
    name = (m.name or "").split("/")[-1]
    acts = list(getattr(m, "supported_actions", None) or [])
    if "imagen" in name or "image" in name:
        rows.append([name, acts])
out(ok=True, models=rows)
'''


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def pick_image_models(rows: list[list[Any]], wanted: str = "") -> dict[str, str]:
    """From [name, actions] rows: the Imagen model (predict) and a Gemini image model (generateContent)."""
    predict = [n for n, acts in rows if "imagen" in n and (not acts or "predict" in acts)]
    content = [n for n, acts in rows if n.startswith("gemini") and "image" in n and (not acts or "generateContent" in acts)]
    imagen = next((n for n in ([wanted] if wanted else []) + list(IMAGEN_PREFER) if n in predict), predict[0] if predict else "")
    gem = next((n for n in GEMINI_IMAGE_PREFER if n in content), content[0] if content else "")
    return {"imagen": imagen, "gemini_image": gem}


def ensure_image_models(force: bool = False) -> dict[str, Any]:
    cached = _read_json(IMAGE_MODELS_PATH)
    if not force and cached.get("checked") and time.time() - float(cached["checked"]) < IMAGE_MODELS_TTL:
        return cached
    try:
        result = _run_okpy(None, "image_models", _MODELS_CODE, [], timeout=60)
    except Exception as exc:  # noqa: BLE001 - keep the last known models
        print(f"image model list failed: {exc}", file=sys.stderr)
        return cached
    rows = result.get("models") or []
    data = {**pick_image_models(rows, os.environ.get("IMAGEN_MODEL", "")), "checked": time.time(),
            "available": [r[0] for r in rows]}
    IMAGE_MODELS_PATH.parent.mkdir(parents=True, exist_ok=True)
    IMAGE_MODELS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return data


def _run_okpy(work, role: str, code: str, args: Any, timeout: int, python: str | None = None,
              no_cover: bool = False) -> dict[str, Any]:
    """Run a short script inside the current site's repo (its venv + .env). Last MARK line is the result.
    no_cover: today's AI picture limit is used up, so okpy writes the post without drawing a cover."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    env = _okpy_env()
    if no_cover:
        for k in ("IMAGEN_MODEL", "NEXT_IMAGEN_OK", "NEXT_GEMINI_IMAGE_MODEL"):
            env.pop(k, None)
        env["IMAGEN_MODEL"] = ""
    log_path = LOG_DIR / f"{role}.log"
    proc = subprocess.Popen(
        [python or _okpy_python(), "-c", code, json.dumps(args, ensure_ascii=False)],
        cwd=str(OKPY),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )
    timer = threading.Timer(timeout, proc.kill)
    timer.start()
    result: dict[str, Any] | None = None
    tail: list[str] = []
    try:
        with log_path.open("w", encoding="utf-8") as log:
            for line in proc.stdout:  # type: ignore[union-attr]
                log.write(line)
                line = line.rstrip()
                if line.startswith(MARK):
                    result = json.loads(line[len(MARK):])
                elif line.strip():
                    tail = (tail + [line])[-12:]
        proc.wait()
    finally:
        timer.cancel()
    if result is None:
        raise RuntimeError(_ko(tail[-1] if tail else f"중간에 멈췄어요 (로그: data/logs/{role}.log)"))
    if not result.get("ok"):
        raise RuntimeError(_ko(result.get("error")))
    return result


# ---------- 서연 · 글 ----------

_WRITE_CODE = r'''
import json, sys
sys.path.insert(0, "scripts")
import generate_posts as g
MARK = "__NEXT_RESULT__"
cat, topic = json.loads(sys.argv[1])
def out(**kw):
    print(MARK + json.dumps(kw, ensure_ascii=False), flush=True)
if not topic:
    queue = g._read_queue(cat, 1)
    if not queue:
        out(ok=False, error="이 분류에 남은 주제가 없어요. 주제를 직접 적어 주세요"); sys.exit(0)
    topic = queue[0]
elif g._topic_already_covered(cat, topic):
    out(ok=False, error="이미 쓴 주제예요"); sys.exit(0)
body = g._generate_body(cat, topic)
if not body:
    out(ok=False, error="글을 만들지 못했어요"); sys.exit(0)
import os
cover = g._generate_cover(cat, topic) if os.getenv("NEXT_IMAGEN_OK") else None
gem = os.getenv("NEXT_GEMINI_IMAGE_MODEL")
if cover is None and gem and os.getenv("GEMINI_API_KEY"):
    # Imagen retired for this key: draw the cover with the Gemini image model, same file/upload steps.
    try:
        from datetime import datetime
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        try:
            cfg = types.GenerateContentConfig(response_modalities=["IMAGE"], image_config=types.ImageConfig(aspect_ratio="16:9"))
        except Exception:
            cfg = types.GenerateContentConfig(response_modalities=["IMAGE"])
        resp = client.models.generate_content(model=gem, contents=g._cover_prompt(cat, topic) + " Wide 16:9 landscape image.", config=cfg)
        raw = None
        for part in (resp.candidates[0].content.parts if resp.candidates else []):
            if getattr(part, "inline_data", None) and part.inline_data.data:
                raw = part.inline_data.data
                break
        if raw:
            name = datetime.now().strftime("%Y%m%d%H%M%S") + ".jpg"
            out_path = g.IMAGES_DIR / name
            g._optimize_cover_jpeg(raw, out_path)
            g._upload_cover_to_gcs(out_path)
            cover = f"{g.GCS_IMAGE_BASE}/{name}"
            print(f"  cover {name} ({gem})", flush=True)
    except Exception as exc:
        print(f"  gemini cover failed: {exc}", flush=True)
path = g._write_md(cat, topic, body, cover=cover)
out(ok=True, topic=topic, title=g._title_from_body(body, topic),
    path=str(path.relative_to(g.POSTS_DIR)), cover=bool(cover))
'''


def write_new(work, category: str, topic: str) -> str:
    _need_content()
    tools.need("claude")
    if not contentkit.is_okpy():
        return contentkit.write_done(contentkit.write(work, category, topic))
    if category not in CATEGORIES:
        raise RuntimeError("분류를 골라 주세요")
    _models_for_covers()
    work.say(f"「{topic}」 글을 쓰는 중" if topic else f"{category} 다음 주제로 글을 쓰는 중")
    result = _run_okpy(work, work.role, _WRITE_CODE, [category, topic.strip()], timeout=60 * 30, no_cover=not _cover_ok())
    return contentkit.write_done(result)


def _cover_ok() -> bool:
    """One AI cover for the next okpy post, if today's picture limit (💰 비용) allows it."""
    from app.money import costs
    try:
        costs.take("image")
        return True
    except costs.OverLimit as exc:
        print(f"cover skipped: {exc}", file=sys.stderr)
        return False


def _models_for_covers() -> None:
    try:
        ensure_image_models()
    except Exception as exc:  # noqa: BLE001 - writing still works without a cover
        print(f"image models: {exc}", file=sys.stderr)


def write_topics(work, items: list[list[str]]) -> str:
    """Manual topics for one writer: [[category, topic], ...] written one by one."""
    _need_content()
    tools.need("claude")
    _models_for_covers()
    done, failed = [], []
    for n, (category, topic) in enumerate(items, 1):
        category = contentkit.resolve_write_category(category, topic)
        work.say(f"{n}/{len(items)}편 「{topic[:30]}」 ({category}) 쓰는 중")
        try:
            result = contentkit.write(work, category, topic)
            if result is None:
                result = _run_okpy(work, work.role, _WRITE_CODE, [category, topic], timeout=60 * 30, no_cover=not _cover_ok())
            done.append(result.get("title") or topic)
        except Exception as exc:  # noqa: BLE001
            failed.append(f"{topic[:20]}: {str(exc)[:60]}")
    if not done:
        if failed and all("이미 쓴 주제" in f for f in failed):
            raise RuntimeError("이미 쓴 주제예요 · " + " / ".join(failed[:2]))
        raise RuntimeError("모두 못 썼어요 · " + " / ".join(failed[:2]))
    return f"{len(done)}편을 썼어요" + (f" · {len(failed)}편 실패" if failed else "")


def _split_frontmatter(text: str) -> tuple[str, str]:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            cut = text.find("\n", end + 4)
            cut = len(text) if cut == -1 else cut + 1
            return text[:cut], text[cut:]
    return "", text


# ---------- 민재 · 검색 ----------

# okadmin's SEO pass sometimes writes its own change note into the post's `summary` (shown on the site).
_MEMO_RE = re.compile(r"CTR|クリック率|掲載順位|位置\s*\d|改善を狙|強化。|に変更|SEO|乗り換え動機|タイトルを|メタ|검색|클릭|제목")


def _undo_memo_summaries(items: list[dict[str, Any]]) -> int:
    """Put back the previous `summary:` line when the new one reads like an internal SEO note."""
    n = 0
    for r in items:
        if r.get("status") != "applied":
            continue
        for f in r.get("files") or []:
            rel = str(f).split(f"{sites.current_id()}/", 1)[-1]
            path = OKPY / rel
            if not rel.endswith(".md") or not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            m = re.search(r"(?m)^summary:\s*(.*)$", text)
            if not m or not _MEMO_RE.search(m.group(1)):
                continue
            old = _git("show", f"HEAD:{rel}")
            om = re.search(r"(?m)^summary:\s*(.*)$", old.stdout) if old.returncode == 0 else None
            if om:
                text = text[:m.start()] + f"summary: {om.group(1)}" + text[m.end():]
            else:  # the post had no summary before: drop the line
                text = text[:m.start()] + text[m.end() + 1:]
            path.write_text(text, encoding="utf-8")
            n += 1
    return n


def search_fix(work, urls: list[str], rows: list[dict[str, Any]] | None = None) -> str:
    _okadmin_import()
    import gsc_url_store
    from gsc_run_store import write_gsc_seo_run
    from gsc_seo_worker import run_seo_jobs

    # okadmin skips a post after 3 tries without change; here the rule is 4 (only in this process).
    gsc_url_store.SEO_ADVICE_DELETE_AFTER = snapshot.SEO_MAX_TRIES

    tools.need("claude")

    work.say(f"글 {len(urls)}편의 제목·설명을 고치는 중")
    rows = [r for r in (rows or []) if r.get("url") in urls]
    pattern_of = {r["url"]: (r.get("pattern") or "low_ctr") for r in rows}
    result = run_seo_jobs(
        sites.current().oid,
        urls=urls,
        apply_files=True,
        url_patterns={u: pattern_of.get(u, "low_ctr") for u in urls},
        gsc_rows=rows or None,
    )
    items = result.get("results") or []
    restored = _undo_memo_summaries(items)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    (LOG_DIR / f"{work.role}.json").write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    seo_ok = bool(items) and any(r.get("status") in ("applied", "no_changes") for r in items)
    try:
        with _SEO_LOCK:
            write_gsc_seo_run(sites.current().oid, result, ok=seo_ok)  # same run record 8090 keeps
    except Exception as exc:  # noqa: BLE001
        print(f"write_gsc_seo_run failed: {exc}", file=sys.stderr)
    applied = sum(1 for r in items if r.get("status") == "applied")
    same = sum(1 for r in items if r.get("status") == "no_changes")
    failed = [r for r in items if r.get("status") not in ("applied", "no_changes")]
    if not items:
        raise RuntimeError(_ko(result.get("error") or "고칠 글을 찾지 못했어요"))
    if failed and not applied and not same:
        raise RuntimeError(_ko(failed[0].get("error") or failed[0].get("status") or "고치지 못했어요"))
    bits = [f"{applied}편을 고쳤어요"]
    if restored:
        bits.append(f"요약(summary)에 작업 메모가 들어간 {restored}편은 원래 요약으로 되돌렸어요")
    if same:
        bits.append(f"{same}편은 바꿀 게 없었어요")
    if failed:
        bits.append(f"{len(failed)}편 실패: {_ko(failed[0].get('error') or failed[0].get('status'))}")
    return " · ".join(bits)


# ---------- 하늘 · 이미지 ----------

_IMAGE_CODE = r'''
import base64, json, os, re, sys
from io import BytesIO
sys.path.insert(0, "scripts")
import generate_posts as g
from PIL import Image, ImageOps
MARK = "__NEXT_RESULT__"
name, hint, backup_dir = json.loads(sys.argv[1])
def out(**kw):
    print(MARK + json.dumps(kw, ensure_ascii=False), flush=True)
path = g.IMAGES_DIR / name
if not path.is_file():
    out(ok=False, error="이미지를 찾지 못했어요"); sys.exit(0)
if not os.getenv("GEMINI_API_KEY"):
    out(ok=False, error="GEMINI_API_KEY missing"); sys.exit(0)
cat = topic = None
for md in g.POSTS_DIR.rglob("*.md"):
    text = md.read_text(encoding="utf-8", errors="ignore")
    if name in text:
        cat = md.parent.name
        m = re.search(r"^title:\s*['\"]?(.+?)['\"]?\s*$", text, re.M)
        topic = m.group(1) if m else md.stem
        break
base = g._cover_prompt(cat, topic) if (cat in g.CATEGORIES and topic) else (
    "clean flat tech illustration for a programming blog, no text, no logos, no watermark, 16:9")
prompt = f"{hint.strip()}. {base}" if hint.strip() else base
from google import genai
from google.genai import types
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
raw, used, errors = None, "", []
imagen = os.getenv("IMAGEN_MODEL") or g.IMAGEN_MODEL
if imagen:
    try:
        resp = client.models.generate_images(
            model=imagen, prompt=prompt,
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
old = Image.open(path)
size, fmt = old.size, (old.format or "JPEG")
os.makedirs(backup_dir, exist_ok=True)
old.save(os.path.join(backup_dir, name), format=fmt)
new = ImageOps.fit(Image.open(BytesIO(raw)).convert("RGB"), size)
new.save(path, format=fmt, quality=86)
out(ok=True, post=topic or "", size=list(size), model=used)
'''


def _gcs_push(names: list[str]) -> str:
    """Send changed images to the site's GCS storage (okadmin's way); never fails the job that changed them."""
    try:
        from app.content import gcsimage
        return gcsimage.push(names)
    except Exception as exc:  # noqa: BLE001
        return f"GCS에 못 올렸어요 ({str(exc)[:120]}) · '☁️ 바뀐 사진 사이트에 올리기'로 다시 올려요"


def _gcs_push_changed() -> str:
    try:
        from app.content import gcsimage
        return gcsimage.push(gcsimage.changed()) if gcsimage.enabled() else ""
    except Exception as exc:  # noqa: BLE001
        return f"GCS에 못 올렸어요 ({str(exc)[:120]}) · '☁️ 바뀐 사진 사이트에 올리기'로 다시 올려요"


_AI_ANY_CODE = r'''
import base64, json, os, sys
from io import BytesIO
from PIL import Image, ImageOps
from google import genai
from google.genai import types
MARK = "__NEXT_RESULT__"
path, hint, title, backup_dir = json.loads(sys.argv[1])
def out(**kw):
    print(MARK + json.dumps(kw, ensure_ascii=False), flush=True)
if not os.path.isfile(path):
    out(ok=False, error="이미지를 찾지 못했어요"); sys.exit(0)
if not os.getenv("GEMINI_API_KEY"):
    out(ok=False, error="GEMINI_API_KEY missing"); sys.exit(0)
base = (f"clean illustration for “{title}”, no text, no logos, no watermark, 16:9"
        if title else "clean flat illustration, no text, no logos, no watermark, 16:9")
prompt = f"{hint.strip()}. {base}" if (hint or "").strip() else base
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
raw, used, errors = None, "", []
imagen = os.getenv("IMAGEN_MODEL") or ""
if imagen:
    try:
        resp = client.models.generate_images(
            model=imagen, prompt=prompt,
            config=types.GenerateImagesConfig(number_of_images=1, aspect_ratio="16:9", output_mime_type="image/png"))
        if resp.generated_images:
            raw, used = resp.generated_images[0].image.image_bytes, imagen
    except Exception as exc:
        errors.append(f"{imagen}: {exc}")
gem = os.getenv("NEXT_GEMINI_IMAGE_MODEL") or ""
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
old = Image.open(path)
size, fmt = old.size, (old.format or "JPEG")
os.makedirs(backup_dir, exist_ok=True)
old.save(os.path.join(backup_dir, os.path.basename(path)), format=fmt)
new = ImageOps.fit(Image.open(BytesIO(raw)).convert("RGB"), size)
new.save(path, format=fmt, quality=86)
out(ok=True, post=title or "", size=list(size), model=used)
'''


DEFAULT_IMAGE_NAMES = ("default.jpg", "default.png")


def default_image_source() -> Path | None:
    """Site stock/default image to paste onto a selected file (repo static/images)."""
    roots: list[Path] = []
    img = Path(sites.IMAGES)
    roots.append(img)
    if img.name in ("posts", "img"):
        roots.append(img.parent)
    repo = sites.current().repo
    for rel in ("app/static/images", "static/images", "app/static/img"):
        roots.append(repo / rel)
    seen: set[Path] = set()
    for root in roots:
        try:
            root = root.resolve()
        except OSError:
            continue
        if root in seen or not root.is_dir():
            continue
        seen.add(root)
        for n in DEFAULT_IMAGE_NAMES:
            p = root / n
            if p.is_file():
                return p
    return None


def images_to_default(work, name: str) -> str:
    """Replace one image with the site's default.jpg/png (same size as before)."""
    dest = snapshot.safe_image(name)
    if dest is None:
        raise RuntimeError("이미지를 찾지 못했어요")
    src = default_image_source()
    if src is None:
        raise RuntimeError("이 사이트에 디폴트 이미지(default.jpg / default.png)가 없어요")
    if src.resolve() == dest.resolve():
        raise RuntimeError("이미 디폴트 이미지예요")
    from PIL import Image, ImageOps
    work.say(f"{name}을 디폴트 이미지로 바꾸는 중")
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    import shutil
    shutil.copy2(dest, BACKUP_DIR / dest.name)
    old = Image.open(dest)
    size, fmt = old.size, (old.format or "JPEG")
    new = ImageOps.fit(Image.open(src).convert("RGB"), size)
    if str(fmt).upper() == "PNG":
        new.save(dest, format="PNG", optimize=True)
    else:
        new.save(dest, format="JPEG", quality=86, optimize=True)
    note = _gcs_push([name])
    return f"{name}을 디폴트({src.name})로 바꿨어요" + (f" · {note}" if note else "")


def images_redraw(work, name: str, hint: str) -> str:
    """AI-draw one selected image. Places photos are only via 후보 고르기."""
    _need_content()
    path = snapshot.safe_image(name)
    if path is None:
        raise RuntimeError("이미지를 찾지 못했어요")
    blocked = contentkit.redraw_blocked()
    if blocked:
        raise RuntimeError(blocked)
    tools.need("gemini")
    from app.money import costs
    costs.take("image")   # 💰 하루 상한
    work.say("쓸 수 있는 이미지 모델을 확인하는 중")
    models = ensure_image_models()
    if not models.get("imagen") and not models.get("gemini_image"):
        models = ensure_image_models(force=True)
    if not models.get("imagen") and not models.get("gemini_image"):
        raise RuntimeError("이 키로 쓸 수 있는 이미지 모델이 없어요 (Imagen·Gemini 이미지 모두 없음)")
    work.say(f"{name} AI로 그리는 중")
    title = ""
    for row in (snapshot._images().get("files") or []):
        if row.get("name") == name:
            title = str(row.get("post") or row.get("label") or "")
            break
    try:
        if contentkit.is_okpy():
            result = _run_okpy(work, work.role, _IMAGE_CODE, [name, hint, str(BACKUP_DIR)], timeout=60 * 5)
        else:
            result = _run_okpy(work, work.role, _AI_ANY_CODE,
                               [str(path), hint, title, str(BACKUP_DIR)], timeout=60 * 5)
    except RuntimeError as exc:
        if "404" in str(exc) or "not found" in str(exc).lower():
            ensure_image_models(force=True)  # model retired since the last check: look again next time
        raise
    post = result.get("post") or title
    note = _gcs_push([name])
    return (f"{name}을 AI로 그렸어요" + (f" (「{post}」)" if post else "") + (f" · {result.get('model')}" if result.get("model") else "")
            + (f" · {note}" if note else ""))


# ---------- 유나 · 이슈/PR, 다은 · 리뷰, 소이 · 머지, 도윤 · 올리기 ----------

def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-c", "core.quotepath=false", "-C", str(OKPY), *args], capture_output=True, text=True, timeout=120)


def issue_pr(work, hint: str) -> str:
    _okadmin_import()
    from github_ops import ship_prepare

    tools.need("claude", "gh")
    wip = snapshot.owner_wip()
    if wip:   # the owner's own uncommitted work must not end up in this PR
        raise RuntimeError(f"사장님이 커밋하지 않은 작업 파일이 있어서 PR을 만들지 않았어요 ({wip[0]} 등 {len(wip)}개). "
                           "커밋하거나 정리한 뒤 다시 맡겨 주세요")

    flow = office.read_flow()
    open_pr = bool(flow.get("pr")) and not flow.get("merged")
    issue_no = (flow.get("issue") or {}).get("number") if open_pr else None
    with work.git_turn():
        snapshot.revert_generated()   # .pyc from running tests / scripts never go into a PR
        # PR already open: add the new changes to it instead of opening a second issue.
        work.say("열린 PR에 바뀐 내용을 더하는 중" if open_pr else "바뀐 내용을 읽고 이슈와 PR을 쓰는 중")
        result = ship_prepare(
            OKPY, site_id=sites.current().oid, hint=hint.strip(),
            issue_number=int(issue_no) if issue_no else None,
            skip_issue=open_pr and not issue_no,
        )
    if not result.get("ok"):
        raise RuntimeError(_ko(result.get("error")))
    pr = result.get("pr") or {}
    office.write_flow(
        pr=pr, issue=result.get("issue") or flow.get("issue"), branch=result.get("branch"),
        reviewed=False, verdict_ok=None, review="", merged=False, deployed=False, review_round=0, required=[], suggestions=[],
        opened_at=snapshot.now_stamp(),
    )
    from app.office import jobs
    # no waiting: the dev leader reviews right away and merges when nothing must be fixed
    who = jobs.start_task("review", f"PR #{pr.get('number')} 리뷰·머지 시작", review_and_merge, _from=work.role)
    if who:
        return f"PR #{pr.get('number')}을 만들었어요 · {office.staff_by_role(who)['name']}에게 리뷰를 넘겼어요"
    return f"PR #{pr.get('number')}을 만들었어요"


def _diff_per_file(diff: str, per_file: int = 3500, total: int = 60000) -> str:
    """Keep every file in the review: cut each file's diff instead of the whole diff."""
    parts = [p for p in diff.split("diff --git ") if p.strip()]
    out = []
    for part in parts:
        chunk = "diff --git " + part
        if len(chunk) > per_file:
            chunk = chunk[:per_file] + f"\n… (이 파일 diff {len(chunk)}자 중 앞부분만)\n"
        out.append(chunk)
    text = "".join(out) or diff
    return text[:total]


def _from_verdict(text: str) -> str:
    """Drop anything the model wrote before the '판정:' line."""
    lines = (text or "").splitlines()
    for i, line in enumerate(lines):
        if line.strip().startswith("판정:"):
            return "\n".join(lines[i:]).strip()
    return (text or "").strip()


MAX_FIX_ROUNDS = 2      # after this many fix → re-review rounds HQ stops and a person decides (never merges by itself)
FOLLOWUPS_PATH = sites.data("followups.md")   # per site


def _is_none(text: str) -> bool:
    """'없음', '-', '없음 (이전 지적은 모두 고쳐짐)', '없음.' — the reviewer saying there is nothing."""
    t = text.strip()
    return t in ("-", "") or t.startswith(("없음", "None", "none"))


def _is_resolved_note(text: str) -> bool:
    """Reviewer leftover under 필수 after '없음': '(이전 필수였던 … 해결됨)' — not a blocker."""
    t = text.strip().lstrip("(").rstrip(").")
    if "이전 필수" in t:
        return True
    if any(w in t for w in ("해결됨", "고쳐졌", "고쳐졌어요", "해결했")) and any(
        w in t for w in ("이전", "필수였", "지적은", "지적이")
    ):
        return True
    return False


def split_review(text: str) -> tuple[list[str], list[str]]:
    """'필수:' items block the merge, '제안:' items don't. Old free-form reviews: every bullet counts as 필수."""
    req: list[str] = []
    sug: list[str] = []
    cur = None
    seen_header = False
    for raw in (text or "").splitlines():
        line = raw.strip()
        if line.startswith("필수"):
            cur, seen_header = req, True
            rest = line.split(":", 1)[1].strip() if ":" in line else ""
            if rest and _is_none(rest):
                cur = None  # '없음' — next bullets are notes, not blockers (until 제안:)
            elif rest and not _is_resolved_note(rest):
                req.append(rest)
            continue
        if line.startswith("제안"):
            cur, seen_header = sug, True
            rest = line.split(":", 1)[1].strip() if ":" in line else ""
            if rest and not _is_none(rest):
                sug.append(rest)
            continue
        if line.startswith(("-", "•", "*")) and cur is not None:
            item = line.lstrip("-•* ").strip()
            if item and not _is_none(item) and not (cur is req and _is_resolved_note(item)):
                cur.append(item)
    if not seen_header:
        req = [ln.strip().lstrip("-•* ").strip() for ln in (text or "").splitlines()[1:] if ln.strip().startswith(("-", "•", "*"))]
    return req, sug


def _save_followups(pr: dict, items: list[str], why: str) -> None:
    if not items:
        return
    FOLLOWUPS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with FOLLOWUPS_PATH.open("a", encoding="utf-8") as fh:
        fh.write(f"\n## PR #{pr.get('number')} · {snapshot.now_stamp()} · {why}\n" + "".join(f"- {x}\n" for x in items))


def review_pr(work) -> str:
    _okadmin_import()
    from github_ops import pr_review
    from llm_claude import MODEL_HEAVY, claude_text

    tools.need("claude", "gh")
    flow = office.read_flow()
    number = (flow.get("pr") or {}).get("number")
    if not number:
        # Stale review after merge: pr_review(main) returns English "on main — nothing to merge"
        synced = sync_merged_pr_from_github()
        raise RuntimeError(synced or "열린 PR이 없어요 · 리뷰할 게 없습니다")
    rnd = int(flow.get("review_round") or 0)
    before = [str(x) for x in (flow.get("required") or [])]
    work.say("PR을 읽는 중" if not rnd else f"고친 PR을 다시 읽는 중 ({rnd}번째 수정)")
    data = pr_review(OKPY, number=number)
    if not data.get("ok"):
        raise RuntimeError(_ko(data.get("error")))
    if data.get("empty"):
        raise RuntimeError("바뀐 내용이 없어서 리뷰할 게 없어요")
    work.say("PR을 읽고 판정 쓰는 중")
    rules = (
        "판정 규칙:\n"
        "- '필수'는 머지 전에 꼭 고쳐야 하는 것만: 빌드·화면이 깨짐, 깨진 마크다운·코드 블록·머리말(YAML), 틀린 사실·코드, 깨진 링크, 보안·개인정보.\n"
        "- 제목 길이, 문구 취향, 톤, 중복 설명, 빈 줄 같은 스타일·SEO 개선은 전부 '제안'이다(머지를 막지 않는다).\n"
        "- 필수가 하나도 없으면 반드시 '판정: 머지 좋아요'.\n"
        "- 필수·제안마다 그 파일의 전체 경로를 diff에 나온 그대로 적어라(… 같은 줄임 금지). 고치는 사람이 그 경로로 파일을 찾는다.\n"
    )
    again = ""
    if rnd and before:
        again = ("이번은 수정 뒤 다시 보는 리뷰다. 이전 리뷰의 필수 항목이 고쳐졌는지와, 이번 수정으로 새로 깨진 것만 '필수'로 본다. "
                 "처음 보는 스타일 지적은 '제안'으로만 쓴다.\n이전 필수 항목:\n" + "".join(f"- {x}\n" for x in before))
    prompt = (
        f"너는 실용적인 리뷰어다. 아래 {sites.current().about} 사이트 PR(글과 사이트 코드 변경)을 한국어로 리뷰해라.\n"
        + rules + again
        + "형식(이대로):\n판정: 머지 좋아요 | 판정: 고칠 게 있어요\n요약: 무엇이 바뀌었는지 한 줄\n필수:\n- … (없으면 '필수: 없음')\n제안:\n- … (최대 4개, 없으면 '제안: 없음')\n\n"
        f"막는 문제: {data.get('blockers') or '없음'}\n경고: {data.get('warnings') or '없음'}\n"
        f"변경 요약:\n{data.get('stat')}\n\n"
        "diff (파일마다 앞부분만 잘라서 보냄 — 잘린 끝부분을 '미완성'으로 판단하지 말 것):\n"
        f"{_diff_per_file(str(data.get('diff') or ''))}"
    )
    text = claude_text(prompt, timeout_sec=600, model=MODEL_HEAVY) or ""
    text = _from_verdict(text)
    if not text:
        raise RuntimeError("리뷰를 쓰지 못했어요")
    required, suggestions = split_review(text)
    ok = not required and not data.get("blockers")
    pr = flow.get("pr") or data.get("pr") or {}
    note = ""
    if not ok and rnd >= MAX_FIX_ROUNDS and not data.get("blockers"):
        # enough rounds: stop and hand it to a person. Merging with required fixes left open
        # is how okonsen/okcaddie lost their search traffic in November 2026.
        _save_followups(pr, required, f"{rnd}번 고친 뒤 남은 항목 · 사람 확인 필요")
        note = f"{rnd}번 고쳤어요 · 남은 {len(required)}개 때문에 머지를 멈췄어요. 사람이 확인해 주세요"
    _save_followups(pr, suggestions, "제안 (머지를 막지 않음)")
    office.write_flow(
        pr=pr if pr else None, reviewed=True, verdict_ok=ok, review=text + (f"\n\n※ {note}" if note else ""),
        required=required, suggestions=suggestions,
        can_merge=bool(data.get("can_merge", True)), reviewed_at=snapshot.now_stamp(),
    )
    if note:
        return note
    return "머지해도 좋아요" if ok else f"꼭 고칠 게 {len(required)}개 있어요"


def _is_merge_conflict_err(err: str) -> bool:
    low = (err or "").lower()
    return any(x in low for x in (
        "not mergeable", "merge conflict", "cannot be cleanly created",
        "conflicting files", "must be resolved", "merge state dirty",
    ))


def merge_pr(work) -> str:
    _okadmin_import()
    from github_ops import merge_pr as gh_merge
    from github_ops import pr_for_branch
    from github_ops import update_pr_branch

    tools.need("gh")

    with work.git_turn():
        flow = office.read_flow()
        number = (flow.get("pr") or {}).get("number")
        if not number:
            found = pr_for_branch(OKPY)
            number = ((found or {}).get("pr") or {}).get("number")
        if not number:
            raise RuntimeError("머지할 PR이 없어요")
        from app.work import seo_guard
        branch = str(flow.get("branch") or "").strip()
        blockers = seo_guard.check_branch(_git, branch) if branch else []
        if flow.get("required"):
            blockers.insert(0, f"리뷰의 꼭 고칠 것 {len(flow.get('required'))}개가 아직 남아 있어요")
        if blockers:
            office.write_flow(verdict_ok=False, seo_blockers=blockers, seo_checked_at=snapshot.now_stamp())
            raise RuntimeError("SEO 안전검사에서 머지를 막았어요: " + " / ".join(blockers)
                               + " · 꼭 필요하면 GitHub에서 사람이 직접 머지해 주세요")
        work.say(f"PR #{number} 머지하는 중")
        result = gh_merge(OKPY, number=int(number))
        if not result.get("ok"):
            # already merged on GitHub (retry after a hang) — still sync local flow
            err = _ko(result.get("error"))
            low = err.lower()
            if "already merged" in low or ("merged" in low and "not mergeable" not in low
                                           and "cannot be cleanly" not in low):
                pass  # treat as success below
            elif _is_merge_conflict_err(err):
                # One shot: pull main into the PR head, then retry merge once.
                work.say(f"PR #{number} 충돌 · main을 합쳐 보는 중")
                upd = update_pr_branch(OKPY, number=int(number))
                if upd.get("ok"):
                    result = gh_merge(OKPY, number=int(number))
                    if result.get("ok"):
                        office.write_flow(merge_conflict=False, merge_conflict_detail="",
                                          merge_conflict_at="")
                        err = ""
                    else:
                        err = _ko(result.get("error"))
                if err and _is_merge_conflict_err(err):
                    tip = (upd.get("error") if isinstance(upd, dict) and not upd.get("ok") else err) or err
                    office.write_flow(
                        merge_conflict=True,
                        merge_conflict_detail=str(tip)[:240],
                        merge_conflict_at=snapshot.now_stamp(),
                    )
                    raise RuntimeError(
                        f"PR #{number} 충돌 · 자동으로 못 맞춰요. 사람이 맞춘 뒤 다시 머지해 주세요 "
                        f"({_ko(tip)[:120]})"
                    )
                if err:
                    raise RuntimeError(err)
            else:
                raise RuntimeError(err)
        # mark merged before pull: local WIP / pull failure must not leave HQ thinking the PR is open
        office.write_flow(merged=True, deployed=False, merged_at=snapshot.now_stamp(), then_common=False,
                          merge_conflict=False, merge_conflict_detail="", merge_conflict_at="")
        # Dirty pages built during the PR often block checkout — same stash path as ensure_on_main.
        if not ensure_on_main_if_idle():
            sw = _git("checkout", "main")
            if sw.returncode != 0:
                sw = _git("checkout", "master")
            if sw.returncode != 0:
                return (f"PR #{number}을 머지했어요 · 로컬 main으로 못 갔어요 "
                        f"({_ko(sw.stderr).strip()[:120] or 'dirty working tree'})")
        pulled = _git("pull", "--ff-only")
        if pulled.returncode != 0:
            return (f"PR #{number}을 머지했어요 · 로컬 main은 받아오지 못했어요 "
                    f"({_ko(pulled.stderr).strip()[:120] or 'dirty working tree'})")
    return f"PR #{number}을 머지했어요"


def _fences(text: str) -> int:
    return sum(1 for ln in text.splitlines() if ln.strip().startswith("```"))


def _extract_file(raw: str, original: str) -> tuple[str, str]:
    """Pull the file out of a model reply. Returns (content, '') or ('', why it was rejected).

    Only a fence wrapped around the whole reply is removed; code blocks inside the post stay.
    """
    text = (raw or "").strip()
    if not text:
        return "", "빈 응답"
    lines = text.splitlines()
    wrapped = False
    if original.startswith("---"):
        starts = [i for i, ln in enumerate(lines) if ln.strip() == "---"]
        if not starts:
            return "", "머리말(---)이 없음"
        wrapped = any(ln.strip().startswith("```") for ln in lines[:starts[0]])
        lines = lines[starts[0]:]  # anything said before the file is dropped
    elif lines and lines[0].strip().startswith("```"):
        wrapped = True
        lines = lines[1:]
    if wrapped:
        while lines and not lines[-1].strip():
            lines.pop()
        if lines and lines[-1].strip() == "```":
            lines.pop()
    body = "\n".join(lines).strip()
    if len(body) < len(original) * 0.5:
        return "", f"너무 짧음 ({len(body)}/{len(original)}자)"
    if _fences(body) != _fences(original) and _fences(original):
        return "", f"코드 블록이 바뀜 (``` {_fences(original)}개 → {_fences(body)}개)"
    return body, ""


_PATH_RE = re.compile(r"(?:app/)?content/(?:posts/)?[^\s`'\"()<>，、]+")   # okpy posts/<section>/x.md · jpcampus content/x.md
_CODE_PATH_RE = re.compile(
    r"(?:`)?((?:app|scripts?|tests)/[^\s`'\")<>，、]+?\.(?:py|js|ts|tsx|css|html|svg|json|ya?ml|toml))"
)
_PATH_TRAIL = re.compile(r"(?:의|을|를|에|에서|은|는|로|으로|과|와|랑|이|가)+$|[.,:;)}」』]+$")


def _clean_path(path: str) -> str:
    p = (path or "").strip().strip("`")
    while True:
        nxt = _PATH_TRAIL.sub("", p)
        if nxt == p:
            return p
        p = nxt


def _paths_in(text: str) -> list[str]:
    return [_clean_path(m.group(0)) for m in _PATH_RE.finditer(text or "") if _clean_path(m.group(0))]


def _code_paths_in(text: str) -> list[str]:
    return [_clean_path(m.group(1) if m.lastindex else m.group(0)) for m in _CODE_PATH_RE.finditer(text or "")]


def _required_points_at_code(required: list[str]) -> bool:
    """True when the blocking items name code files (not only markdown posts). Mixed PRs must go to fix_code."""
    blob = "\n".join(required or [])
    code = _code_paths_in(blob)
    posts = _paths_in(blob)
    if code and not posts:
        return True
    if code and posts:
        return len(code) >= len(posts)
    return False


_ANY_PATH_RE = re.compile(
    r"(?:`)?((?:app|scripts?|tests|output|data)/[^\s`'\")<>，、]+)"
)
_AGENT_BLOCKED_PREFIX = ("output/", "data/", "app/content/", "app/static/images/")


def _any_paths_in(text: str) -> list[str]:
    return [_clean_path(m.group(1)) for m in _ANY_PATH_RE.finditer(text or "") if _clean_path(m.group(1))]


def _required_only_blocked(required: list[str]) -> bool:
    """True when 필수 only names paths the fix agent is denied (output/logos, content, …)."""
    paths = _any_paths_in("\n".join(required or []))
    if not paths:
        return False
    return all(
        p.startswith(_AGENT_BLOCKED_PREFIX) or any(f"/{b}" in f"/{p}" for b in _AGENT_BLOCKED_PREFIX)
        for p in paths
    )


def _looks_already_fixed(text: str) -> bool:
    t = (text or "").lower()
    keys = ("이미", "필요 없", "추가 수정이 필요", "반영되어", "고칠 게 없", "해결됨", "already", "nothing to")
    return any(k in (text or "") or k in t for k in keys)


def match_review_files(items: list[str], posts: list[str]) -> list[dict[str, str]]:
    """Find the PR's post files a review item points at, even when the reviewer shortened the path
    ("python/python...starlette...md") or only quoted the text: path pieces must appear in order in the
    real path (or the file's text), and a quoted phrase (8+ letters) found in a file points at it."""
    out: list[dict[str, str]] = []

    def add(post: str, item: str) -> None:
        if all(o["path"] != post for o in out):
            out.append({"path": post, "fix": item[:300]})

    texts: dict[str, str] = {}

    def text_of(post: str) -> str:
        if post not in texts:
            try:
                texts[post] = (OKPY / post).read_text(encoding="utf-8")
            except OSError:
                texts[post] = ""
        return texts[post]

    for item in items:
        found = False
        for written in _paths_in(item):
            pieces = [x.strip("/") for x in re.split(r"\.{2,}|…", written.replace("app/", "", 1)) if x.strip("/")]
            if pieces and pieces[-1].endswith(".md"):
                pieces[-1] = pieces[-1][:-3]
            pieces = [x for x in pieces if x]
            for post in posts:
                hay, at, ok = post + "\n" + text_of(post).lower(), 0, bool(pieces)
                for piece in pieces:
                    i = hay.find(piece, at) if at < len(post) else -1
                    if i < 0:
                        i = hay.find(piece.lower(), len(post))   # e.g. "starlette" only in the article
                    if i < 0:
                        ok = False
                        break
                    at = i + len(piece)
                if ok:
                    add(post, item)
                    found = True
        if found:
            continue
        quotes = re.findall(r'["“「`]([^"”」`]{8,})["”」`]', item)
        for q in quotes:
            for piece in (x.strip() for x in re.split(r"\.{2,}|…", q)):
                if len(piece) < 8:
                    continue
                for post in posts:
                    if piece in text_of(post):
                        add(post, item)
    return out


def fix_from_review(work) -> str:
    """서연: fix the files the review points at, commit + push to the same PR, then hand back to 다은."""
    tools.need("claude", "gh")
    _okadmin_import()
    from git_ops import git_commit_only, git_push_only
    from llm_claude import MODEL_HEAVY, MODEL_LIGHT, claude_json, claude_text

    flow = office.read_flow()
    required = [str(x) for x in (flow.get("required") or [])]
    review = ("꼭 고칠 것(이것만 고친다):\n" + "\n".join(f"- {x}" for x in required)) if required else str(flow.get("review") or "").strip()
    branch = str(flow.get("branch") or "").strip()
    if not flow.get("pr") or flow.get("merged") or not review:
        raise RuntimeError("고칠 리뷰가 없어요. Dev 리더(Daeun)에게 리뷰를 먼저 맡겨 주세요")
    fixed: list[str] = []
    skipped: list[str] = []
    with work.git_turn():
        synced = sync_merged_pr_from_github()
        if synced:
            return synced
        ensure_pr_branch(branch)
        names = [n.strip() for n in _git("diff", "--name-only", "main...HEAD").stdout.splitlines() if n.strip()]
        posts = [n for n in names if n.endswith(".md") and (OKPY / n).is_file()]
        if not posts:
            raise RuntimeError("PR에 고칠 글 파일이 없어요")
        work.say("리뷰에서 고칠 파일을 고르는 중")
        listing = "\n".join(f"- {n}" for n in posts)
        pick = claude_json(
            "다음 PR 리뷰에서 실제로 고쳐야 하는 파일만 골라라. 파일 목록 밖은 고르지 마라.\n"
            f"리뷰:\n{review}\n\n파일:\n{listing}\n\n"
            'JSON만: {"files": [{"path": "...", "fix": "이 파일에서 고칠 점 한 줄"}]}',
            timeout_sec=180, model=MODEL_LIGHT,
        ) or {}
        todo = [f for f in (pick.get("files") or []) if isinstance(f, dict) and f.get("path") in posts][:8]
        if not todo:   # the reviewer shortened the path ("python/python...starlette...md"): match the pieces
            todo = match_review_files(required or [review], posts)[:8]
        if not todo:
            raise RuntimeError("리뷰에 적힌 파일을 PR에서 찾지 못했어요 (경로: " + (_paths_in(review)[:1] or ["없음"])[0][:80]
                               + "). 리뷰를 보고 직접 고쳐 주세요")
        for n, item in enumerate(todo, 1):
            path = OKPY / item["path"]
            original = path.read_text(encoding="utf-8")
            work.say(f"{n}/{len(todo)} {path.stem[:24]} 고치는 중")
            new = claude_text(
                "아래 블로그 글 파일(Markdown, 머리말 포함)을 리뷰 지적대로 고쳐라.\n"
                f"이 파일에서 고칠 점: {item.get('fix') or ''}\n"
                f"전체 리뷰(참고):\n{review}\n\n"
                "규칙: 머리말(---로 감싼 부분)을 포함한 파일 전체를 출력한다. 머리말의 lang과 본문·메타데이터 언어를 맞춘다. "
                "지적되지 않은 내용·코드·링크·이미지는 그대로 둔다. 설명 없이 파일 내용만 출력한다.\n\n"
                f"----- {item['path']} -----\n{original}",
                timeout_sec=900, model=MODEL_HEAVY,
            ) or ""
            fix_log = LOG_DIR / "fix"
            fix_log.mkdir(parents=True, exist_ok=True)
            (fix_log / f"{path.stem[:60]}.txt").write_text(new, encoding="utf-8")
            body, why = _extract_file(new, original)
            if why:
                skipped.append(f"{path.stem[:20]}: {why}")
                continue
            path.write_text(body + "\n", encoding="utf-8")
            fixed.append(item["path"])
        if not fixed:
            raise RuntimeError("하나도 저장하지 않았어요 · " + " / ".join(skipped[:3]) + " (받은 내용: data/logs/fix/)")
    from app.office import jobs
    who = jobs.start_task("push", f"고친 {len(fixed)}개 파일 푸시 시작", push_fixes, fixed, _from=work.role)
    if who:
        return f"{len(fixed)}개 파일을 고쳤어요 · {office.staff_by_role(who)['name']}에게 푸시를 넘겼어요"
    return f"{len(fixed)}개 파일을 고쳤어요 · 개발팀이 바빠요. 끝나면 '푸시' 버튼을 눌러 주세요"


def ensure_pr_branch(branch: str) -> None:
    """Switch the site repo onto the open PR branch. Call inside work.git_turn()."""
    branch = (branch or "").strip()
    if not branch:
        return
    cur = _git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if cur == branch:
        return
    sw = _git("checkout", branch)
    if sw.returncode != 0:
        _git("fetch", "origin", branch, timeout=120)
        sw = _git("checkout", branch)
    if sw.returncode != 0:
        raise RuntimeError(f"PR 브랜치 {branch}로 못 바꿨어요: {_ko(sw.stderr)}")


def ensure_on_main_if_idle() -> bool:
    """When no open PR is tracked, put the site repo back on main/master.

    Deploy and content jobs expect main. After an outside merge (or a leftover
    feat/ checkout), local HEAD often stays on the feature branch and every
    deploy fails with 「main에서만 올려요」 for days. Returns True if switched.

    Dirty local files (company pages built while the PR was open) often block
    checkout — stash them, switch, pull, then pop so we do not stay stuck.
    """
    flow = office.read_flow()
    if flow.get("pr") and not flow.get("merged"):
        return False
    cur = _git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if cur in ("main", "master", "HEAD", ""):
        return False

    def _checkout_prod():
        """Try main then master. Return (ok_proc_or_last_fail, combined_err_text)."""
        errs: list[str] = []
        sw = _git("checkout", "main")
        errs.append(_ko(sw.stderr) + " " + _ko(sw.stdout))
        if sw.returncode == 0:
            return sw, " ".join(errs)
        sw2 = _git("checkout", "master")
        errs.append(_ko(sw2.stderr) + " " + _ko(sw2.stdout))
        return sw2, " ".join(errs)

    sw, err_blob = _checkout_prod()
    stashed = False
    if sw.returncode != 0:
        err = err_blob.lower()
        blocked = any(
            s in err
            for s in (
                "local changes",
                "would be overwritten",
                "commit your changes",
                "please commit",
                "untracked working tree",
            )
        )
        if blocked:
            stash = _git("stash", "push", "-u", "-m", "hq: switch to main after merge")
            if stash.returncode == 0 and "No local changes to save" not in (stash.stdout or ""):
                stashed = True
                sw, err_blob = _checkout_prod()
        if sw.returncode != 0:
            if stashed:
                _git("stash", "pop")
            print(f"ensure_on_main_if_idle: {_ko(sw.stderr) or err_blob.strip()}", file=sys.stderr)
            return False
    _git("pull", "--ff-only")
    if stashed:
        pop = _git("stash", "pop")
        if pop.returncode != 0:
            print(f"ensure_on_main_if_idle stash pop: {_ko(pop.stderr)}", file=sys.stderr)
    return True


def sync_merged_pr_from_github() -> str | None:
    """If flow still says the PR is open but GitHub already merged it, mark merged locally.

    Stops autoship from looping forever on fix_review after a manual / outside merge.
    Returns a short status message when synced, else None.
    """
    flow = office.read_flow()
    # Stale: PR already cleared / deployed but merged flag never flipped
    if not flow.get("pr") and not flow.get("merged") and (flow.get("deployed") or flow.get("merged_at") or flow.get("last_pr")):
        office.write_flow(merged=True)
        ensure_on_main_if_idle()
        return None
    pr = flow.get("pr") if isinstance(flow.get("pr"), dict) else None
    number = (pr or {}).get("number")
    if not number or flow.get("merged"):
        ensure_on_main_if_idle()
        return None
    tools.need("gh")
    proc = subprocess.run(
        ["gh", "pr", "view", str(int(number)), "--json", "state,mergedAt"],
        cwd=str(OKPY), capture_output=True, text=True, timeout=60,
    )
    if proc.returncode != 0:
        # Deleted branch / unknown PR after squash-merge — treat as merged when branch is gone
        err = (proc.stderr or proc.stdout or "").lower()
        if "could not find" in err or "not found" in err or "no pull requests" in err:
            office.write_flow(merged=True, deployed=False, merged_at=snapshot.now_stamp(), then_common=False,
                              merge_conflict=False, merge_conflict_detail="", merge_conflict_at="")
            ensure_on_main_if_idle()
            return f"PR #{number}을 찾지 못했어요(이미 머지·삭제된 듯) · 배포 차례로 넘길게요"
        return None
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return None
    if str(data.get("state") or "").upper() != "MERGED" and not data.get("mergedAt"):
        return None
    office.write_flow(merged=True, deployed=False, merged_at=snapshot.now_stamp(), then_common=False,
                      merge_conflict=False, merge_conflict_detail="", merge_conflict_at="")
    ensure_on_main_if_idle()
    return f"PR #{number}은 이미 머지됐어요 · 배포 차례로 넘길게요"


def push_fixes(work, fixed: list[str] | None = None) -> str:
    """도윤: commit + push the review fixes to the open PR branch, then hand it back to 다은."""
    tools.need("gh")
    _okadmin_import()
    from git_ops import git_commit_only, git_push_only

    flow = office.read_flow()
    branch = str(flow.get("branch") or "").strip()
    with work.git_turn():
        synced = sync_merged_pr_from_github()
        if synced:
            return synced
        ensure_pr_branch(branch)
        if not _git("status", "--porcelain").stdout.strip():
            # nothing changed: count it as a round anyway, or 자동 진행 hands out the same fix forever
            office.write_flow(review_round=int(flow.get("review_round") or 0) + 1)
            raise RuntimeError("푸시할 변경이 없어요")
        work.say("고친 내용을 커밋하는 중")
        n = len(fixed or [])
        done = git_commit_only(OKPY, site_id=sites.current().oid, message=f"Apply review fixes ({n} files)" if n else "Apply review fixes")
        if not done.get("ok"):
            raise RuntimeError(_ko(done.get("error")))
        work.say(f"PR 브랜치에 푸시하는 중")
        pushed = git_push_only(OKPY, site_id=sites.current().oid)
        if not pushed.get("ok"):
            raise RuntimeError(_ko(pushed.get("error")))
    flow = office.read_flow()
    office.write_flow(reviewed=False, verdict_ok=None, review="", fixed_at=snapshot.now_stamp(), last_fixed=fixed or [],
                      review_round=int(flow.get("review_round") or 0) + 1)
    from app.office import jobs
    # re-review checks only the required items; when they are fixed it merges by itself (the cycle ends)
    who = jobs.start_task("review", "고친 PR 다시 리뷰 시작", review_and_merge, False, False, _from=work.role)
    name = office.staff_by_role(who)["name"] if who else "개발팀"
    return f"PR에 푸시했어요 · {name}에게 다시 리뷰를 넘겼어요"


def _pr_has_posts(flow: dict) -> bool:
    """Does the open PR change blog posts (the only files fix_from_review can fix)?"""
    branch = str(flow.get("branch") or "").strip()
    if not branch:
        return False
    names = _git("diff", "--name-only", f"main...{branch}").stdout.splitlines()
    root = sites.current().posts_rel.strip("/") + "/"
    return any(n.strip().endswith(".md") and n.strip().startswith(root) for n in names)


def start_fix_for_review(_from: str = "") -> tuple[str | None, str]:
    """Hand open-PR review fixes to the right team. Returns (who, kind) where kind is
    fix_code | fix | needs_person. Code 필수 (even on mixed PRs) → fix_code."""
    from app.office import jobs
    from app.work import devwork

    flow = office.read_flow()
    required = [str(x) for x in (flow.get("required") or [])]
    if required and _required_only_blocked(required):
        # Never merge with required fixes left open: a person decides.
        _save_followups(flow.get("pr") or {}, required, "에이전트가 못 고치는 파일 · 사람 확인 필요")
        # park the PR so 자동 진행 does not hand it out again every loop
        office.write_flow(review_round=max(int(flow.get("review_round") or 0), MAX_FIX_ROUNDS), needs_person=True)
        jobs._refused(f"PR #{(flow.get('pr') or {}).get('number', '?')}은 에이전트가 못 고치는 지적 {len(required)}개가 남아 머지를 멈췄어요 · "
                      "사람이 고치거나 GitHub에서 판단해 주세요 (followups.md)", "person")
        return None, "needs_person"
    need_code = _required_points_at_code(required) or not _pr_has_posts(flow)
    if need_code:
        return jobs.start_task("fix_code", "리뷰 지적 코드 고치기 시작", devwork.fix_review, _from=_from), "fix_code"
    return jobs.start_task("fix", "리뷰 지적 고치기 시작", fix_from_review, _from=_from), "fix"


def review_and_merge(work, force: bool = False, review_only: bool = False) -> str:
    """다은: read the PR and judge it; when it's fine (or force), merge it right away.
    review_only: after 서연's fixes, judge again but leave the merge for you to press."""
    if not force:
        verdict = review_pr(work)
        flow = office.read_flow()
        if not flow.get("verdict_ok"):
            # hand the required fixes on by itself; the round limit in review_pr ends the cycle
            if int(flow.get("review_round") or 0) >= MAX_FIX_ROUNDS:
                return f"{verdict} · 머지는 멈췄어요 (막는 문제가 있어요. 리뷰를 봐 주세요)"
            who, kind = start_fix_for_review(_from=work.role)
            if kind == "needs_person":
                return f"{verdict} · 에이전트가 못 고치는 항목이라 머지를 멈췄어요. 리뷰를 봐 주세요"
            if who:
                person = office.staff_by_role(who)["name"]
                if kind == "fix_code":
                    return f"{verdict} · {person}에게 코드 고치기를 넘겼어요"
                return f"{verdict} · {person}에게 고치기를 넘겼어요"
            if kind == "fix_code":
                return f"{verdict} · 개발팀이 바빠요. 끝나면 '개발팀에 고치기 맡기기'를 눌러 주세요"
            return f"{verdict} · 마케팀이 바빠요. 끝나면 '고치기 맡기기'를 눌러 주세요"
        from app.work import autoship
        if review_only or not (sites.current().automerge or autoship.enabled()):   # 자동 진행 off: a person presses merge
            return f"{verdict} · 머지 버튼을 눌러 주세요"
    return merge_pr(work)


def deploy(work) -> str:
    _okadmin_import()
    from git_ops import git_current_branch, is_production_branch, start_deploy, wait_for_deploy_job

    tools.need("gcloud")
    wip = snapshot.owner_wip()
    if wip:   # Cloud Build uploads the folder as it is: the owner's unfinished work would go live
        raise RuntimeError(f"사장님이 커밋하지 않은 작업 파일이 있어서 올리지 않았어요 ({wip[0]} 등 {len(wip)}개). "
                           "커밋하거나 정리한 뒤 다시 맡겨 주세요")

    with work.git_turn():
        branch = git_current_branch(OKPY)
        if not is_production_branch(OKPY, branch):
            raise RuntimeError(f"지금 브랜치가 {branch}예요. main에서만 올려요. 소이에게 머지를 먼저 맡겨 주세요")
        work.say("사이트를 올리는 중 (Cloud Build)")
        started = start_deploy(OKPY, site_id=sites.current().oid, mode="deploy-only", with_git=False,
                               with_deploy=True, include_build_data=False)
        if not started.get("ok"):
            raise RuntimeError(_ko(started.get("error")))
        final = wait_for_deploy_job(started["job_id"], site_id=sites.current().oid, timeout=60 * 40)
    if not final.get("ok"):
        reason = final.get("error_summary") or final.get("message") or final.get("error") or "올리지 못했어요"
        raise RuntimeError(f"{_ko(reason)} (로그: {started.get('log_path')})")
    flow = office.read_flow()
    if flow.get("merged"):
        office.write_flow(pr=None, reviewed=False, verdict_ok=None, merged=False, deployed=True,
                          last_pr=flow.get("pr"), deployed_at=snapshot.now_stamp())
    else:
        office.write_flow(deployed=True, deployed_at=snapshot.now_stamp())
    return "사이트에 올렸어요"


# ---------- 지호 · 리서치 → 서연 · 글 (okadmin trends → topic bank → generate_posts) ----------

RESEARCH_PATH = sites.data("research.json")   # per site (okpy: data/…)
_RESEARCH_LOCK = threading.RLock()   # research.json is updated by several writers at once
_BANK_LOCK = threading.Lock()        # okadmin topic bank state (priority_enqueue / refresh)
_SEO_LOCK = threading.Lock()         # okadmin gsc run record
# okadmin topic bank id → okpy posts category (generate_posts.py argument)
BANK_TO_CATEGORY = {
    "python": "python",
    "cloud": "cloud",
    "terraform": "terraform",
    "dev_method": "dev-method",
    "data_analysis": "data-analysis",
    "eng_comms": "eng-comms",
    "ai_models": "ai-models",
    "career": "career",
}
BANK_LABEL = {
    "python": "Python 라이브러리",
    "cloud": "클라우드 비교",
    "terraform": "Terraform",
    "dev_method": "개발방법론",
    "data_analysis": "Business Analysis (영문)",
    "eng_comms": "엔지니어 소통",
    "ai_models": "AI 모델 비교",
    "career": "Career",
}


def read_research(raw: bool = False) -> dict[str, Any]:
    """The last research and its picks. CompanyDB also lists its add-queue (companies queued without a page)
    as ready picks, unless raw (what is really saved in research.json)."""
    try:
        data = json.loads(RESEARCH_PATH.read_text(encoding="utf-8"))
        data = data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        data = {}
    return data if raw else contentkit.enrich_research(data)


def _save_research(data: dict[str, Any]) -> None:
    RESEARCH_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = RESEARCH_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(RESEARCH_PATH)


def seed_options() -> dict[str, Any]:
    """Seed keywords okadmin recommends for Trends (JA/KO/EN + defaults).

    OKPy: Japanese only, aligned to writable categories (`OKPY_CATEGORY_SEEDS`).
    """
    if contentkit.is_okpy():
        ja = contentkit.okpy_seed_keywords(24)
        # One seed from each writable category as the default checked set.
        defaults = [
            next(iter(contentkit.OKPY_CATEGORY_SEEDS.get(c) or ()), "")
            for c in contentkit.OKPY_CATEGORIES
        ]
        defaults = [d for d in defaults if d][:8]
        return {
            "site_id": sites.current().oid,
            "defaults": defaults,
            "ja": ja,
            "ko": [],
            "en": [],
            "by_category": {
                c: list(contentkit.OKPY_CATEGORY_SEEDS.get(c) or ())
                for c in contentkit.OKPY_CATEGORIES
            },
        }
    try:
        _okadmin_import()
        from trends_topic_ai import seed_recommendations_for_site

        return seed_recommendations_for_site(sites.current().oid)
    except Exception:  # noqa: BLE001
        return {"defaults": [], "ja": [], "ko": [], "en": []}


def seed_chips(limit: int = 20) -> dict[str, Any]:
    """Keywords shown on the write screen: a pool of up to `limit` (default 20) to pick from.

    OKPy: Japanese · category-aligned round-robin. Other sites: JA/KO/EN round-robin.
    """
    if contentkit.is_okpy():
        opts = seed_options()
        pool = contentkit.okpy_seed_keywords(limit)
        defaults = [str(x).strip() for x in (opts.get("defaults") or []) if str(x).strip()]
        seen = {w.lower() for w in pool}
        # One keyword per writable category checked → research covers all banks.
        checked = [w for w in defaults if w.lower() in seen][: len(contentkit.OKPY_CATEGORIES)] or pool[:3]
        return {"all": pool, "checked": checked, "by_category": opts.get("by_category") or {}}

    opts = seed_options()
    defaults = [str(x).strip() for x in (opts.get("defaults") or []) if str(x).strip()]
    langs = [
        [str(x).strip() for x in (opts.get(k) or []) if str(x).strip()]
        for k in ("ja", "ko", "en")
    ]
    pool: list[str] = []
    seen: set[str] = set()

    def _add(word: str) -> bool:
        key = word.lower()
        if not word or key in seen:
            return False
        seen.add(key)
        pool.append(word)
        return len(pool) >= limit

    for w in defaults:
        if _add(w):
            break
    if len(pool) < limit:
        i = 0
        while len(pool) < limit:
            progressed = False
            for lang in langs:
                if i < len(lang) and _add(lang[i]):
                    progressed = True
                    if len(pool) >= limit:
                        break
                elif i < len(lang):
                    progressed = True
            if not progressed:
                break
            i += 1
    checked = [w for w in defaults if w.lower() in seen][:3] or pool[:3]
    return {"all": pool, "checked": checked}


def ensure_analysis_picks() -> None:
    """Fold GA4·GSC analysis topics into the assignable picks list (idempotent).

    CompanyDB: only listed companies (ticker+market). Guide-style strings and
    already-covered tickers are dropped from the list.
    OKPy: never — 맡길 목록은 카테고리 리서치 픽만 (남은 analysis 픽은 제거).
    """
    from app.content import contentkit
    from app.work import marketing

    topics = marketing.read_analysis().get("topics") or []
    with _RESEARCH_LOCK:
        data = read_research(raw=True)
        picks = list(data.get("picks") or [])
        changed = False

        if contentkit.is_okpy():
            kept = [p for p in picks if p.get("source") != "analysis"]
            if len(kept) != len(picks):
                picks = kept
                changed = True
        elif contentkit.is_companydb():
            from app.content import cdbcontent

            have = cdbcontent.covered()
            skip = cdbcontent.skipped_tickers()
            kept: list[dict[str, Any]] = []
            for p in picks:
                if p.get("source") != "analysis":
                    kept.append(p)
                    continue
                row = p.get("row") if isinstance(p.get("row"), dict) else {}
                t = str(row.get("ticker") or "").strip().upper()
                if not t or t in have or t in skip:
                    changed = True
                    continue  # guide leftover, already on site, or no filings
                kept.append({**p, "row": {**row, "ticker": t}, "topic": p.get("topic") or f"{t} {row.get('name') or t}"})
            picks = kept
            existing_tickers = {
                str((p.get("row") or {}).get("ticker") or "").strip().upper()
                for p in picks if (p.get("row") or {}).get("ticker")
            }
            for item in topics:
                if not isinstance(item, dict):
                    continue
                m = str(item.get("market") or "").upper()
                t = str(item.get("ticker") or "").strip().upper()
                if m not in cdbcontent.CATEGORIES or not t or t in have or t in skip or t in existing_tickers:
                    continue
                name = str(item.get("name_en") or t)[:80]
                pid = f"ana-{t.lower()}"
                picks.append({
                    "id": pid,
                    "topic": f"{t} {name}",
                    "category": m,
                    "row": {"market": m, "ticker": t, "name": name},
                    "source": "analysis",
                    "kind": "분석 추천",
                    "bank_label": cdbcontent.MARKET_LABEL[m],
                    "status": "new",
                    "why": str(item.get("why") or "GA4·GSC 분석에서 나온 회사")[:200],
                })
                existing_tickers.add(t)
                changed = True
        else:
            strings: list[str] = []
            for t in topics:
                if isinstance(t, dict):
                    continue  # company-shaped topics belong on CompanyDB only
                s = str(t).strip()
                if s:
                    strings.append(s)
            cats = categories()
            default_cat = cats[0] if cats else "python"
            existing = {str(p.get("topic") or "").strip().lower() for p in picks}
            for topic in strings:
                key = topic.lower()
                if key in existing:
                    continue
                pid = f"ana-{abs(hash(key)) % 10**10}"
                picks.append({
                    "id": pid,
                    "topic": topic[:200],
                    "category": default_cat,
                    "source": "analysis",
                    "kind": "분석 추천",
                    "bank_label": "분석 추천",
                    "status": "new",
                    "why": "GA4·GSC 분석에서 나온 주제",
                })
                existing.add(key)
                changed = True

        if changed:
            data["picks"] = picks
            _save_research(data)


def pick_groups_for_write(research: dict[str, Any] | None = None) -> list[dict[str, Any]] | None:
    """OKPy: every writable category → fresh research picks (empty groups included). Else None.

    data-analysis group = English drafts to write. Japanese convert is separate (`ba_ja_pending`).
    """
    from app.content import contentkit

    if not contentkit.is_okpy():
        return None
    data = research if isinstance(research, dict) else read_research()
    label_of = {cat: BANK_LABEL.get(bank, cat) for bank, cat in BANK_TO_CATEGORY.items()}
    by_cat: dict[str, list[dict[str, Any]]] = {c: [] for c in contentkit.OKPY_CATEGORIES}
    for p in data.get("picks") or []:
        if p.get("status") != "new" or p.get("source") == "analysis":
            continue
        cat = str(p.get("category") or "").strip()
        if cat in by_cat:
            by_cat[cat].append(p)
    return [
        {"category": c, "label": label_of.get(c, c), "picks": by_cat[c]}
        for c in contentkit.OKPY_CATEGORIES
    ]


def ba_ja_pending(limit: int = 20) -> list[dict[str, str]]:
    """English Business Analysis drafts waiting for Japanese (write screen, separate from EN picks)."""
    from app.content import contentkit

    if not contentkit.is_okpy():
        return []
    posts = OKPY / "app" / "content" / "posts" / "data-analysis"
    if not posts.is_dir():
        return []
    out: list[dict[str, str]] = []
    for path in sorted(posts.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if not (re.search(r"(?m)^lang:\s*en\s*$", text) or "ja_pending: true" in text):
            continue
        title = path.stem
        m = re.search(r"(?m)^title:\s*(.+)\s*$", text)
        if m:
            title = m.group(1).strip().strip("'\"")
        rel = str(path.relative_to(OKPY)).replace("\\", "/")
        out.append({"name": path.name, "title": title, "path": rel})
        if len(out) >= limit:
            break
    return out


def _keywords_from_snapshot(snap: dict[str, Any] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for seed, block in ((snap or {}).get("by_seed") or {}).items():
        for kind in ("rising", "top"):
            for row in (block or {}).get(kind) or []:
                q = str((row or {}).get("query") or "").strip()
                if q and q.lower() not in seen:
                    seen.add(q.lower())
                    out.append({"word": q, "from": f"트렌드 {'급상승' if kind == 'rising' else '인기'} · {seed}",
                                "value": int((row or {}).get("value") or 0)})
    return out


def _keywords_from_seeds(seeds: list[str] | None, *, source: str = "선택한 시드") -> list[dict[str, Any]]:
    """Chip seeds as keyword rows (CompanyDB / Trends empty fallback)."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in seeds or []:
        w = str(raw or "").strip()
        key = w.lower()
        if not w or key in seen:
            continue
        seen.add(key)
        out.append({"word": w, "from": source, "value": 0})
    return out


def _editorial_keywords() -> list[dict[str, Any]]:
    try:
        data = json.loads((OKPY / "data" / "it_trends.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    out = []
    for board in data.get("boards") or []:
        for item in (board or {}).get("items") or []:
            name = str((item or {}).get("name") or "").strip()
            if name:
                out.append({"word": name, "from": f"OKPy 트렌드 · {board.get('title') or board.get('id') or ''}".strip(" ·"), "value": 0})
            if len(out) >= 10:
                return out
    return out


def _search_keywords(limit: int = 6) -> list[dict[str, Any]]:
    try:
        snap = snapshot.load_or_refresh()
        rows = ((snap.get(sites.current_id()) or {}).get("search") or {}).get("rows") or []
    except Exception:  # noqa: BLE001
        return []
    out = []
    for row in rows[:limit]:
        word = str(row.get("path") or "").strip()
        if word:
            out.append({"word": word, "from": "우리 글 검색 노출", "value": int(row.get("impressions") or 0)})
    return out


OKPY_MIN_PER_CATEGORY = 2  # 맡길 목록: 허용 카테고리마다 최소 이만큼


def _okpy_make_pick(bank: str, value: str, *, keyword: str = "", why: str = "", idx: int = 0) -> dict[str, Any]:
    cat = BANK_TO_CATEGORY[bank]
    field = "lib_name" if bank == "python" else "Topic"
    pick = {
        "id": f"p{int(time.time())}{idx}",
        "bank": bank, "kind": BANK_LABEL[bank], "bank_label": BANK_LABEL[bank], "category": cat,
        "row": {field: value}, "topic": value,
        "keyword": keyword, "why": why, "status": "new",
    }
    if cat == "data-analysis":
        pick["needs_ja"] = True
    return pick


def _okpy_parse_topic_items(
    items: list[Any],
    existing: dict[str, set[str]],
    seen: set[str],
    start_idx: int = 0,
) -> list[dict[str, Any]]:
    picks: list[dict[str, Any]] = []
    for i, item in enumerate(items or []):
        bank = str((item or {}).get("bank") or "").strip()
        row = (item or {}).get("row") or {}
        if bank not in BANK_TO_CATEGORY or not isinstance(row, dict):
            continue
        field = "lib_name" if bank == "python" else "Topic"
        value = str(row.get(field) or "").strip()
        if not value or value.lower() in existing.get(bank, set()) or value.lower() in seen:
            continue
        cat = BANK_TO_CATEGORY[bank]
        if topic_already_covered(cat, value):
            continue
        seen.add(value.lower())
        picks.append(_okpy_make_pick(
            bank, value,
            keyword=str(item.get("keyword") or ""),
            why=str(item.get("why") or ""),
            idx=start_idx + i,
        ))
    return picks


def _okpy_fill_category_gaps(
    picks: list[dict[str, Any]],
    existing: dict[str, set[str]],
    keywords: list[dict[str, Any]],
    claude_json,
    model: str,
    per_cat: int = OKPY_MIN_PER_CATEGORY,
) -> list[dict[str, Any]]:
    """Ensure each writable bank has at least `per_cat` picks (second Claude pass for gaps)."""
    from collections import Counter

    counts = Counter(str(p.get("bank") or "") for p in picks)
    need: list[tuple[str, int]] = []
    for bank in BANK_TO_CATEGORY:
        gap = per_cat - counts.get(bank, 0)
        if gap > 0:
            need.append((bank, gap))
    if not need:
        return picks
    seen = {str(p.get("topic") or "").lower() for p in picks}
    kw_lines = "\n".join(
        f"- {k['word']} ({k['from']}{', ' + str(k['value']) if k.get('value') else ''})"
        for k in keywords[:24]
    )
    need_lines = "\n".join(f"- {bank}: need {n} more" for bank, n in need)
    avoid = "\n".join(
        f"{bank}: {', '.join(sorted(existing.get(bank, set()) | {t for t in seen}) )[:400]}"
        for bank, _ in need
    )
    bank_list = "|".join(b for b, _ in need)
    total = sum(n for _, n in need)
    prompt = f"""You are the editor of OKPy. Fill MISSING category quotas only.
Need exactly these additional topics (total {total}):
{need_lines}

Keywords (Japanese search intent):
{kw_lines}

Field format per bank:
- python: {{"lib_name": "LibraryName"}}
- cloud / terraform / ai_models / data_analysis: {{"Topic": "English topic"}}
- data_analysis topics must be English (JA convert later)
- dev_method / eng_comms / career: {{"Topic": "日本語トピック"}}

Do NOT duplicate:
{avoid}

Return ONLY JSON:
{{"topics": [{{"bank": "{bank_list}", "row": {{...}}, "keyword": "...", "why": "한국어 한 줄"}}]}}"""
    data = claude_json(prompt, timeout_sec=240, model=model) or {}
    extra = _okpy_parse_topic_items(data.get("topics") or [], existing, seen, start_idx=len(picks) + 10)
    # Keep only banks that still need slots
    still = {b: n for b, n in need}
    filled: list[dict[str, Any]] = []
    for p in extra:
        b = p["bank"]
        if still.get(b, 0) <= 0:
            continue
        filled.append(p)
        still[b] -= 1
    return picks + filled


def research(work, seeds: list[str], auto_n: int, n_topics: int = 8) -> str:
    """Trends → keywords → Claude picks topics for OKPy banks → recommendation list (→ 서연)."""
    _need_content()
    tools.need("claude")
    _okadmin_import()
    from llm_claude import MODEL_HEAVY, claude_json
    from okpy_topic_ai import _existing_python, _existing_topics
    from trends_topic_ai import load_trends_snapshot, normalize_seed_picks, run_trends_seed

    if contentkit.is_okpy() and not seeds:
        seeds = list(seed_options().get("defaults") or [])
    picked = normalize_seed_picks(seeds, max_n=20) if seeds else None
    if contentkit.is_okpy():
        n_topics = max(int(n_topics or 0), len(BANK_TO_CATEGORY) * OKPY_MIN_PER_CATEGORY)
    work.say("구글 트렌드에서 검색어를 찾는 중")
    trend_note = ""
    try:
        info = run_trends_seed(sites.current().oid, seeds=picked, append_bank=False)  # same call as 8090 '트렌드 조회'
        if not info.get("ok"):
            trend_note = _ko(info.get("error"))
    except Exception as exc:  # noqa: BLE001 - Trends often rate-limits; keep going with other signals
        trend_note = _ko(exc)
    keywords = _keywords_from_snapshot(load_trends_snapshot(sites.current().oid)) + \
        (_editorial_keywords() if contentkit.uses_editorial_keywords() else []) + _search_keywords()
    # User-picked chips always count (Trends may return empty related queries).
    seen_kw = {str(k.get("word") or "").lower() for k in keywords}
    for row in _keywords_from_seeds(picked):
        if row["word"].lower() not in seen_kw:
            keywords.append(row)
            seen_kw.add(row["word"].lower())
    if not keywords and contentkit.is_okpy():
        keywords = _keywords_from_seeds(
            contentkit.okpy_seed_keywords(16), source="OKPy 카테고리 시드",
        )
    if not keywords and contentkit.is_companydb():
        opts = seed_options()
        fallback = list(opts.get("defaults") or []) + list(opts.get("en") or []) + list(opts.get("ko") or [])
        keywords = _keywords_from_seeds(fallback[:12], source="CompanyDB 시드")
    if not keywords:
        raise RuntimeError("키워드를 하나도 못 찾았어요. " + (trend_note or ""))
    work.say(contentkit.research_say(len(keywords)))
    kit_picks = contentkit.research_picks(keywords, n_topics, claude_json)
    if kit_picks is not None:
        return _research_done(kit_picks, picked, trend_note, keywords, auto_n)

    existing = {bank: (_existing_python() if bank == "python" else _existing_topics(bank))
                for bank in BANK_TO_CATEGORY}
    kw_lines = "\n".join(f"- {k['word']} ({k['from']}{', ' + str(k['value']) if k['value'] else ''})" for k in keywords[:24])
    avoid = "\n".join(f"{bank} ({len(v)}): {', '.join(sorted(v)[:120])}" for bank, v in existing.items())
    bank_list = "|".join(BANK_TO_CATEGORY)
    per = OKPY_MIN_PER_CATEGORY
    prompt = f"""You are the editor of OKPy, a Japanese tech blog.
Allowed categories only: Python, Cloud, Terraform, 開発方法論, Business Analysis, エンジニアのコミュニケーション, AIモデル比較, Career.
Do NOT suggest Data Model, PMBOK, Agile&Scrum, or Fit Journey.
Below are search keywords. Recommend {n_topics} NEW article topics related to them.
HARD RULE: at least {per} topics for EVERY bank ({", ".join(BANK_TO_CATEGORY)}). Spread evenly; do not dump into one bank.

Keywords:
{kw_lines}

Each topic must fit one bank and use that bank's field format:
- python: {{"lib_name": "LibraryName"}}  (one Python library)
- cloud: {{"Topic": "AWS X vs GCP Y vs Azure Z ..."}}  (English)
- terraform: {{"Topic": "Terraform ..."}}  (English)
- dev_method: {{"Topic": "日本語の開発手法トピック"}}  (Japanese)
- data_analysis: {{"Topic": "English effect-size / A-B test insight topic"}}  (English only — JA translation is a later step)
- eng_comms: {{"Topic": "日本語の状況トピック"}}  (Japanese)
- ai_models: {{"Topic": "GPT-x vs Claude y vs Gemini z for ..."}}  (English)
- career: {{"Topic": "日本語のIT職種・年収・ロードマップトピック"}}  (Japanese)

Do NOT duplicate existing entries:
{avoid}

Return ONLY JSON:
{{"topics": [{{"bank": "{bank_list}", "row": {{...}}, "keyword": "which keyword above", "why": "한국어 한 줄: 왜 지금 이 주제인가"}}]}}"""
    data = claude_json(prompt, timeout_sec=300, model=MODEL_HEAVY) or {}
    seen: set[str] = set()
    picks = _okpy_parse_topic_items(data.get("topics") or [], existing, seen)
    work.say("카테고리별 추천이 부족한 칸을 채우는 중")
    picks = _okpy_fill_category_gaps(picks, existing, keywords, claude_json, MODEL_HEAVY, per_cat=per)
    return _research_done(picks, picked, trend_note, keywords, auto_n)


def _research_done(picks: list[dict[str, Any]], picked: Any, trend_note: str, keywords: list[dict[str, Any]], auto_n: int) -> str:
    if not picks:
        raise RuntimeError(f"{sites.josa(sites.current().words['picks'], '을/를')} 고르지 못했어요. 다시 맡겨 주세요")
    _save_research({
        "at": snapshot.now_stamp(), "seeds": picked or seed_options().get("defaults") or [],
        "trend_note": trend_note, "keywords": keywords[:24], "picks": picks,
    })
    done = f"주제 {len(picks)}개를 찾았어요"
    if auto_n > 0:
        import jobs  # local import: jobs imports workers' callers, not workers

        ids = [p["id"] for p in picks[:auto_n]]
        w = sites.current().words
        started = jobs.start_task_split("write", ids, lambda n: f"{w['picks']} {n}{w['unit']} {w['verb']} 시작", write_picks)
        if started:
            names = ", ".join(office.staff_by_role(r)["name"] for r, _ in started)
            done += f" · {names}에게 {len(ids)}편 넘겼어요"
        else:
            done += " · 기획팀이 모두 바빠서 목록에 두었어요"
    return done


def _set_pick(pick_id: str, **changes: Any) -> None:
    """Update a pick in research.json. `upsert=` materializes queue/virtual picks so errors stick."""
    seed = changes.pop("upsert", None)
    with _RESEARCH_LOCK:
        data = read_research(raw=True)
        picks = list(data.get("picks") or [])
        hit = next((p for p in picks if p.get("id") == pick_id), None)
        if hit is None and seed is not None:
            hit = {**seed, "id": pick_id}
            picks.append(hit)
        if hit is not None:
            hit.update(changes)
            data["picks"] = picks
            _save_research(data)


def find_and_queue_companies(work, n: int = 10) -> str:
    """CompanyDB: staff picks listed companies (no owner typing) → universe_extra."""
    _need_content()
    if not contentkit.is_companydb():
        raise RuntimeError("CompanyDB만 회사 찾기를 해요")
    from app.content import cdbcontent
    from llm_claude import MODEL_HEAVY, claude_json

    tools.need("claude")
    _okadmin_import()
    n = max(3, min(15, int(n or 10)))
    # System seeds only — owner does not pick keywords or type company names.
    opts = seed_options()
    seeds = list(opts.get("defaults") or []) + list(opts.get("en") or []) + list(opts.get("ko") or [])
    keywords = _keywords_from_seeds(seeds[:12], source="CompanyDB 시드") + _search_keywords()
    if not keywords:
        keywords = _keywords_from_seeds(
            ["japanese stocks", "korean stocks", "annual report", "10-K", "EDINET"],
            source="기본",
        )
    work.say(contentkit.research_say(len(keywords)))
    picks = cdbcontent.picks_from(
        claude_json(cdbcontent.research_prompt(keywords, n), timeout_sec=300, model=MODEL_HEAVY) or {},
        n=n,
    )
    work.say("시장 비율(JP 60% · KR 20% · US 20%) 맞추는 중")
    picks = cdbcontent.fill_market_gaps(picks, keywords, n, claude_json, MODEL_HEAVY)
    if not picks:
        raise RuntimeError("추가할 회사를 고르지 못했어요. 다시 맡겨 주세요")
    done: list[str] = []
    failed: list[str] = []
    for pick in picks:
        row = pick.get("row") or {}
        market = str(row.get("market") or pick.get("category") or "").upper()
        ticker = str(row.get("ticker") or "").strip()
        name = str(row.get("name") or "")
        if market not in cdbcontent.CATEGORIES or not ticker:
            continue
        try:
            work.say(f"{ticker} {name} 대기열에 넣는 중")
            out = cdbcontent.enqueue_ticker(market, ticker, name_en=name)
            label = f"{out.get('market') or market} {out.get('ticker') or ticker}"
            if out.get("built"):
                failed.append(f"{label}: 이미 페이지 있음")
            else:
                done.append(label)
        except Exception as exc:  # noqa: BLE001
            failed.append(f"{ticker}: {_ko(exc)[:80]}")
    # Keep the list on the write screen (queue picks merge on read; also stash research picks).
    _save_research({
        "at": snapshot.now_stamp(), "seeds": seeds[:8], "trend_note": "",
        "keywords": keywords[:24], "picks": picks,
    })
    if not done:
        raise RuntimeError("대기열에 못 넣었어요" + (f" · {failed[0]}" if failed else ""))
    note = f" · {len(failed)}곳 건너뜀" if failed else ""
    if failed:
        print("find_and_queue fails: " + " / ".join(failed[:4]), file=sys.stderr)
    return f"{len(done)}곳 찾아 대기열에 넣었어요" + note


def write_picks(work, ids: list[str]) -> str:
    """서연 writes the picked topics one by one. Each goes through okadmin's priority queue first."""
    _need_content()
    tools.need("claude")
    _models_for_covers()
    _okadmin_import()

    picks = {p["id"]: p for p in (read_research().get("picks") or [])}
    todo = [picks[i] for i in ids if i in picks]
    if not todo:
        raise RuntimeError("쓸 주제가 없어요. 기획팀에게 리서치를 먼저 맡겨 주세요")
    written, failed = [], []

    def _drop_unbuildable(pick: dict[str, Any], err: str) -> None:
        if not contentkit.is_companydb():
            return
        from app.content import cdbcontent
        if not cdbcontent.unbuildable_error(err):
            return
        row = pick.get("row") if isinstance(pick.get("row"), dict) else {}
        m = str(row.get("market") or pick.get("category") or "").upper()
        t = str(row.get("ticker") or "").strip()
        if m and t:
            try:
                cdbcontent.mark_skipped(
                    m, t, reason=err[:200], name=str(row.get("name") or pick.get("topic") or "")[:120],
                )
                cdbcontent.drop_from_queue(m, t)
            except Exception as exc:  # noqa: BLE001
                print(f"mark_skipped/drop_from_queue failed: {exc}", file=sys.stderr)

    # CompanyDB JP: one EDINET scan before builds — no yuho → fail + leave add-queue.
    if contentkit.is_companydb():
        from app.content import cdbcontent

        jp_todo = []
        for p in todo:
            row = p.get("row") if isinstance(p.get("row"), dict) else {}
            m = str(row.get("market") or p.get("category") or "").upper()
            t = str(row.get("ticker") or "").strip()
            if m == "JP" and t:
                jp_todo.append((p, t))
        if jp_todo:
            try:
                ready, _missing = cdbcontent.edinet_ready_tickers(work, [t for _, t in jp_todo])
            except Exception as exc:  # noqa: BLE001 - fall through to per-ticker build errors
                print(f"edinet preflight failed: {exc}", file=sys.stderr)
                ready = {t for _, t in jp_todo}
            keep = []
            for p in todo:
                row = p.get("row") if isinstance(p.get("row"), dict) else {}
                m = str(row.get("market") or p.get("category") or "").upper()
                t = str(row.get("ticker") or "").strip()
                if m == "JP" and t and t not in ready:
                    err = "EDINET에서 최근 유가증권보고서를 찾지 못했어요"
                    _set_pick(p["id"], status="failed", error=err, upsert=p)
                    _drop_unbuildable(p, err)
                    failed.append(f"{p['topic'][:24]}: {err}")
                else:
                    keep.append(p)
            todo = keep

    for n, pick in enumerate(todo, 1):
        _set_pick(pick["id"], status="writing", upsert=pick)
        if contentkit.uses_topic_bank() and pick.get("bank") and pick.get("row"):
            try:
                from topic_bank import priority_enqueue

                with _BANK_LOCK:
                    priority_enqueue(sites.current().oid, pick["bank"], [pick["row"]])  # same bookkeeping 8090 does
            except Exception as exc:  # noqa: BLE001
                print(f"priority_enqueue failed: {exc}", file=sys.stderr)
        work.say(f"{n}/{len(todo)}편 「{pick['topic']}」 쓰는 중")
        try:
            result = contentkit.write(work, pick["category"], pick["topic"], row=pick.get("row"))
            if result is None:
                result = _run_okpy(work, work.role, _WRITE_CODE, [pick["category"], pick["topic"]], timeout=60 * 30,
                                   no_cover=not _cover_ok())
            path = result.get("path") or ""
            _set_pick(pick["id"], status="written", path=path, title=result.get("title"),
                      needs_ja=bool(pick.get("needs_ja") or pick.get("category") == "data-analysis"))
            written.append(result.get("title") or pick["topic"])
            if pick.get("category") == "data-analysis" and path:
                try:
                    from app.work import workqueue
                    workqueue.enqueue({
                        "source": "task",
                        "task": "ba_ja",
                        "title": f"BA 일본어화 · {pick['topic'][:40]}",
                        "label": "Business Analysis 영→일",
                        "site": sites.current_id(),
                        "paths": [path],
                    })
                    workqueue.kick(sites.current_id())
                except Exception as exc:  # noqa: BLE001
                    print(f"ba_ja enqueue failed: {exc}", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 - keep going with the rest
            err = str(exc)[:200]
            _set_pick(pick["id"], status="failed", error=err)
            _drop_unbuildable(pick, err)
            failed.append(f"{pick['topic'][:24]}: {err}")
    try:
        if contentkit.uses_topic_bank():
            from topic_bank_pipeline import refresh_topic_state

            with _BANK_LOCK:
                refresh_topic_state(sites.current().oid, OKPY)
    except Exception as exc:  # noqa: BLE001
        print(f"refresh_topic_state failed: {exc}", file=sys.stderr)
    if not written:
        why = " · ".join(failed[:2]) if failed else ""
        raise RuntimeError(f"{len(failed)}편 모두 못 썼어요" + (f" · {why}" if why else ""))
    note = f" · {len(failed)}편 실패" if failed else ""
    if failed:
        note += " · " + failed[0][:80]
    return f"{len(written)}편을 썼어요" + note


def translate_ba_ja(work, rels: list[str] | None = None) -> str:
    """Business Analysis: convert English drafts (lang: en) to Japanese — separate from writing."""
    _need_content()
    if not contentkit.is_okpy():
        raise RuntimeError("okpy만 Business Analysis 일본어화를 해요")
    tools.need("claude")
    sys.path.insert(0, str(OKPY / "scripts"))
    import generate_posts as g  # noqa: WPS433

    posts_root = OKPY / "app" / "content" / "posts"
    paths: list[Path] = []
    if rels:
        for rel in rels:
            rel_s = str(rel).lstrip("/")
            for cand in (posts_root / rel_s, OKPY / rel_s, Path(rel_s)):
                if cand.is_file():
                    paths.append(cand)
                    break
    if not paths:
        paths = g.list_en_data_analysis(limit=8)
    if not paths:
        raise RuntimeError("일본어로 바꿀 영어 BA 초안이 없어요")
    done, failed = [], []
    for n, path in enumerate(paths, 1):
        work.say(f"{n}/{len(paths)} · {path.name} 일본어로 옮기는 중")
        try:
            out = g.translate_data_analysis_to_ja(path)
            done.append(out.name)
        except Exception as exc:  # noqa: BLE001
            failed.append(f"{path.name}: {str(exc)[:80]}")
    if not done:
        raise RuntimeError("일본어화에 모두 실패했어요 · " + (failed[0] if failed else ""))
    note = f" · {len(failed)}편 실패" if failed else ""
    return f"BA {len(done)}편을 일본어로 옮겼어요" + note



def images_places_missing(work) -> str:
    """krcampus: photos for school/university pages that have none (Google Places)."""
    _need_content()
    from app.content import jpcontent
    if contentkit.is_poi():
        from app.content import poicontent
        out = poicontent.places_missing(work)
        snapshot.refresh_snapshot()
        return out
    if not jpcontent.has_places():
        raise RuntimeError("이 사이트는 Google Places 사진을 쓰지 않아요")
    out = jpcontent.places_missing(work)
    note = _gcs_push_changed()
    snapshot.refresh_snapshot()
    return out + (f" · {note}" if note else "")



def images_replace_stock(work) -> str:
    """Campus sites: guides still on Unsplash get a Google Places / school photo cover."""
    _need_content()
    from app.content import jpcontent
    if not jpcontent.is_site():
        raise RuntimeError("이 사이트에서는 쓸 수 없어요")
    out = jpcontent.replace_stock_covers(work)
    note = _gcs_push_changed()
    snapshot.refresh_snapshot()
    return out + (f" · {note}" if note else "")



def images_fill_logos(work) -> str:
    """companydb: company logos for pages that have none (the site's own logo step)."""
    _need_content()
    from app.content import cdbcontent
    if not cdbcontent.is_site():
        raise RuntimeError("CompanyDB에서만 쓸 수 있어요")
    out = cdbcontent.fill_logos(work)
    snapshot.refresh_snapshot()
    return out


def partner_collect(work) -> str:
    """제휴 공개 메일 수집 (URL 채움 → 메일 없는 곳 최대 partners.COLLECT_LIMIT)."""
    from app import partners as partners_mod

    sid = sites.current_id()
    if sid not in partners_mod.enabled_sites():
        raise RuntimeError("이 사이트는 제휴 메일 목록이 없어요")
    noun = (partners_mod.cfg(sid) or {}).get("noun") or "업체"
    no_mail = len(partners_mod.items(sid, has_email="0"))
    if not no_mail:
        return f"메일 없는 {noun}이(가) 없어요"
    work.say(f"{noun} 공개 메일 수집 · 최대 {partners_mod.COLLECT_LIMIT}곳")
    out = partners_mod.prepare_and_collect(sid, limit=partners_mod.COLLECT_LIMIT, say=work.say)
    if not out.get("ok") and not out.get("tried"):
        return str(out.get("msg") or "웹사이트 있는 곳이 없어요")
    bits = [f"공개 메일 {out['found']}곳 찾음 · 시도 {out['tried']}곳"]
    if out.get("from_content"):
        bits.append(f"콘텐츠 URL {out['from_content']}")
    if out.get("from_places"):
        bits.append(f"Places URL {out['from_places']}")
    bits.append(str(out.get("at") or ""))
    return " · ".join(bits)


def partner_collect_floor(work) -> str:
    """이 층 제휴 사이트 전부 · Places 한도 나눠 사이트당 수집."""
    from app import partners as partners_mod

    oid = sites.current().office
    sids = partners_mod.floor_site_ids(oid)
    if not sids:
        raise RuntimeError("이 층에 제휴 메일 사이트가 없어요")
    lim = partners_mod.floor_per_site_limit(len(sids))
    off = sites.OFFICES.get(oid)
    floor = off.floor_label if off else oid
    work.say(f"{floor} 제휴 메일 수집 · {len(sids)}사이트 · 사이트당 최대 {lim}")
    found = tried = 0
    parts: list[str] = []
    for sid in sids:
        with sites.use(sid):
            label = (partners_mod.cfg(sid) or {}).get("noun") or sid
            no_mail = len(partners_mod.items(sid, has_email="0"))
            if not no_mail:
                parts.append(f"{sid}: 메일 다 있음")
                continue
            work.say(f"—— {sid} ({label}) ——")
            out = partners_mod.prepare_and_collect(sid, limit=lim, say=work.say)
            found += int(out.get("found") or 0)
            tried += int(out.get("tried") or 0)
            parts.append(f"{sid}: 찾음 {out.get('found', 0)}/{out.get('tried', 0)}")
    return f"{floor} 공개 메일 {found}곳 찾음 · 시도 {tried}곳 · " + " · ".join(parts)
