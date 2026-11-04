# Starful HQ 개발 사양 (SPEC)

> 비즈니스 요건(목적·대상·업무 흐름·화면 요구·규칙의 이유·결정)은 기획서 `Google Drive/내 드라이브/AI/Starful HQ/Starful HQ 기획서.html`.
> 화면 설계는 Claude Design 캔버스 「Starful HQ 화면 설계」(https://claude.ai/artifact/4euhfrt6Xw6yUAfQB8Dwu2), 기획서 11장에도 같은 화면.
> 이 문서는 개발 사양만: 실행 환경 · 코드 구조 · 데이터 · 라우트 · 상수 · 구현 규칙 · 테스트 · 알려진 문제.

2026-11-04 기획서에서 분리 · @스타풀

## 1. 실행 환경

- Mac 로컬 `/opt/work/starful-hq`, 포트 8091 (http://127.0.0.1:8091)
- 기동: `./start.sh` → `python -m app` (Flask). stat 리로더라 `.py`를 고치면 서버가 재시작되고 돌던 잡은 끊김. 템플릿은 `TEMPLATES_AUTO_RELOAD`로 재시작 없이 반영
- okadmin(8090)은 수정하지 않고 읽기·import만 사용 (`git_ops` · `github_ops` · `gsc_seo_worker` · `gsc_run_store` · `gsc_url_store` · 트렌드 · 토픽 뱅크). `workers._okadmin_import()`로 경로 추가
- 사이트 목록: `/opt/work/sites.yaml` (okadmin과 공유)
- 비밀값: GCP Secret Manager(프로젝트 `starful-258005`) 우선, 없으면 `okadmin/.env` (`app/gsecrets.py`)
- 외부 도구: Claude CLI · `gh` · `gcloud` · Gemini 키 (`app/tools.py`가 점검, `tools.need()`로 잡 시작 전 확인)

## 2. 코드 구조

```
starful-hq/
  start.sh
  app/
    __main__.py        python -m app 진입점
    routes.py          화면·POST 라우트 (STEPS · TEAM_TABS · STEP_TAB)
    sites.py           sites.yaml 로드 · Office · floor_label · MAX_CONCURRENT
    snapshot.py        홈/사이트 스냅샷 · 이미지 목록(_image_sig/_image_kind/_image_label) · owner_wip · SEO 재시도
    tools.py           Claude CLI · gh · gcloud · Gemini 키 점검
    gsecrets.py · paths.py
    office/            __init__(배치·팀·자리·B1 borrow) · jobs · meeting · boss
    work/              workqueue · autoship · autodev · devwork · workers · actions · planning · marketing
    content/           contentkit · jpcontent · cdbcontent · poicontent · placepick · gcsimage · imagecleanup · youtube_api · youtube_queue
    money/             costs · affiliate · affsync · usage
    templates/         site.html · home.html · _*.html 조각 (_office_map · _money_card · _affiliate_card · _costs_card …)
    static/            admin.css · office/ (floor-*.svg · floor-front-*.svg · nav-*.json)
  data/                런타임 (아래 4장)
  tests/               17개 파일 · 104개 테스트
  .bak/                예전 수동 백업 (git 이전, 추적 안 함)
```

## 3. 사이트 레지스트리 (`sites.yaml`)

- `hq_offices`: `{id: {name, blurb, floor}}`. floor 음수 = 지하 (`floor_label` → `"B1"`)
- `services[].hq`: `office · order · enabled · content · posts_rel · images_rel · flat · blurb`
  - `enabled: false` = 소프트 끔, `hq:` 삭제 = HQ에서 뺌
  - `content: ""` = 글쓰기 없음 (CONTENT_TASKS 불가)
  - `youtube: {enabled, noun, page_prefix, query}` = 디자인 탭 YouTube 모드
- 정렬: `_load_hq` 키 `(floor < 1, floor, id)` → 위층 먼저, 그다음 지하
- `sites.py`에 사이트 목록을 하드코드하지 않음. 테스트 `tests/test_sites.py`

| 사이트 | office | order | content |
|---|---|---|---|
| okpy | datalab | 10 | okpy_posts |
| jpcampus | datalab | 20 | campus_guides |
| krcampus | datalab | 30 | campus_guides |
| companydb | datalab | 40 | companydb |
| okramen | playground | 10 | poi |
| okcaddie | playground | 20 | poi |
| okonsen | playground | 30 | poi |
| jpfun | playground | 40 | poi |
| krcare | basement | 10 | "" |

### 3-1. contentkit (`app/content/contentkit.py`)

content 타입별 쓰기·리서치·slug·레이아웃. UI seed/topics는 kit의 `seed_hint`/`topics_hint` → `cur_site.words`.

| content | 구현 | 하는 일 |
|---|---|---|
| okpy_posts | okpy `generate_posts` | okadmin 트렌드·토픽 뱅크(`BANK_TO_CATEGORY`) → 리서치 → 카테고리별 글 |
| campus_guides | `content/jpcontent.py` | 학교 가이드 · 번역 · Gemini/Imagen 커버 |
| companydb | `content/cdbcontent.py` | EDINET/DART/SEC 공시 → 기업 페이지 · 로고 |
| poi | `content/poicontent.py` | Google Places 조회 → Kit 필드 (okramen·okcaddie·okonsen·jpfun) |
| "" | — | 없음 (krcare) |

## 4. 데이터 (`data/`)

| 파일 | 내용 |
|---|---|
| `sites/{id}/work_queue.json` | 실행 큐 |
| `sites/{id}/prefs.json` | `queue_paused` |
| `sites/{id}/research.json` | 리서치 결과 (`_RESEARCH_LOCK`) |
| `autoship.json` | `offices.<office>.on` (층 단위 토글) · 사이트별 `checked_at` |
| `wip_baseline.json` | 사장님 미커밋 파일 기준 (처음 본 시점) |
| `proposals/{id}.json/.md` | 회의 결과. 층 전체 회의는 `offices/{office}/proposals` |
| `spend_limits.json` | 하루·월 한도 (현재 이미지 10 · Places 1000 · 월 ¥1000 · YouTube 80) |
| `hq_usage.json` | HQ 화면 클릭 사용량 |
| `boss.json` | 사장 로그인 허용 이메일 |
| `home.json` 등 | 홈 스냅샷 (새로고침·작업 종료 후 다시 셈) |
| `image_backups/cleanup/` | 이미지 정리 백업 |
| `youtube/{site}_candidates.json` | YouTube 후보 캐시 |
| `followups.md` | 리뷰 후속 과제 |

## 5. 라우트

| 영역 | GET / POST |
|---|---|
| 공통 | `/` · `/office` · `/job` · `/staff` · `/usage` · `/usage/track` · `/healthz` · `/login` · `/oauth/callback` · `/logout` · `/boss/move` · `/refresh` · `/site-icon/{sid}` |
| 현황·회의 | `/site/{id}` · `/meetings` · `meeting` · `meeting/top` · `meeting/run` · `propose` · `proposal/{pid}.md` · `autoship` · `/office/{office}/autoship` · `checked` · `queue/pause` |
| 기획 | `write` · `write/research` · `write/picks` · `write/new` |
| 마케 | `search` · `search/analyze` · `search/clear` · `polish` · `analysis/{aid}.md` · `analytics/card` |
| 디자인 | `images` · `images/covers` · `logos` · `places` · `candidates` · `candidate` · `pick` · `redraw` · `sync` · `cleanup` · `file/{name}` · `youtube`(→`images#youtube`) · `youtube/api/…` |
| 개발 | `ship` · `ship/issue` · `review` · `fix` · `push` · `merge` · `refactor` · `auto` |
| 카드·도구 | `costs` · `costs/limits` · `affiliate(/{what})` · `tools/check` · `tools/claude-login` · `tools/restart` · `run/{task}` · `action/{aid}` · `panel/{team}` |

- 사이트 라우트는 `/site/{id}/…`. `_OPEN_BEFORE_CONNECT = {site_status, site_costs_card, site_affiliate_card}`
- 탭: `TEAM_TABS` = status/plan(write)/mkt(search, polish 포함)/design(images)/dev(ship). `STEP_TAB["meet"] = ""` (회의는 탭 아님)
- B1(`office.is_borrowed()`)에서 `site_meeting`은 `_back("status")`

## 6. 오피스 · 잡 (`app/office/`)

- `STAFF = {"datalab": _STAFF_1F, "playground": _STAFF_2F, "basement": ()}`
- `TASK_TEAM`: plan = research · write · issue · propose / mkt = search · polish · analyze · fix / design = images / dev = fix_code · refactor* · optimize · review · push · deploy
- `TASK_GROUP` (사이트당 한 명): refactor 그룹(refactor·refactor_front·refactor_back·optimize) · pr 그룹(issue·review·fix·fix_code·push·merge) · deploy 그룹
- `ITEM_TASKS = (write, search, polish)` · `OPTIONAL_TASKS = (write, search)` · `AWAY_TASKS = (research, review, propose, analyze)` → 프리석/핫데스크
- `jobs.py`: 사원 1명당 잡 1개 · `MAX_CONCURRENT = NEXT_MAX_JOBS(기본 6)` · 저장소별 git 잠금(`work.git_turn()`)
  - `WIP_TASKS = (issue, push, deploy, optimize, refactor, refactor_front, refactor_back)` → `snapshot.owner_wip()`가 있으면 시작 안 함
  - `CONTENT_TASKS = (research, write, images)` → kit의 `can_write` 필요
- B1: `_LAYOUT_B1` (`borrow: True`, `max_visitors: 2`, 핫데스크 12) · `BORROW_TEAM = {"plan": "dev"}` · `is_borrowed()` · `_job_office()` · `visitors()` · `_borrowed_members()` · `build_office`가 위층 사원을 `away` 처리 · 지도 위치 키 `okoffice.pos.b` · 테스트 `tests/test_b1.py`
- 지도: 6:5 비율, `nav-*.json` 격자 위를 걷기, 바닥 SVG + 앞쪽 SVG 두 겹

## 7. 실행 큐 (`app/work/workqueue.py`)

- 상태: queued → running → done / failed / held
- `LOOP_SEC = 60` · `KICK_DELAY = 4.0` (잡 종료 후 다음 시작)
- held: 열린 PR이 막는 제안 → 머지 후 queued
- `sweep_zombies`: 재시작으로 끊긴 running → failed(「잡이 끊김 · 다시 담아 주세요」)
- 검색 백로그 항상 켬 · 회의 액션 일괄 담기 `MAX_BATCH = 15` (`actions.py`, `routes.py`)

## 8. 자동 진행 (`autoship.py` · `workers.py`)

- `LOOP_SEC = 180` · `KICK_DELAY = 4.0`. 꺼진 층은 스킵
- stage: changes → pr → (고치기) → deploy → check / stuck / idle
- changes: `issue_pr` (okadmin `github_ops.ship_prepare`)
- pr: `review_and_merge`. 리뷰는 Claude가 「판정: / 요약 / 필수 / 제안」 줄 → `split_review`
  - `_is_none`: 「필수: 없음 (설명)」은 없음으로 · `_is_resolved_note`: 「이전 필수…해결됨」 제외
  - 고치기 입구 `start_fix_for_review`: 코드 필수 → `devwork.fix_review`(fix_code) · 글만 → `fix_from_review`(fix)
  - `MAX_FIX_ROUNDS = 2` 후 머지, 남은 건 `followups.md`. 변경 없는 push도 1회로 셈
  - 에이전트 금지 경로만 남으면 후속 이슈로 두고 머지
  - GitHub에서 이미 머지됐으면 로컬 pull 실패와 무관하게 `flow.merged = True`
- deploy (`workers.deploy`): `owner_wip()` 있으면 중단 · main 브랜치만(`is_production_branch`) · okadmin `start_deploy(mode="deploy-only")` + `wait_for_deploy_job(timeout=40분)` · 성공 시 `write_flow(deployed=True, deployed_at)`
- check: 사람이 `checked` POST

### 8-1. 자동 최적화 (`autodev.py`)

- `CHECK_SEC = 15분` · 첫 확인 3분 · `MIN_GAP = 24h` · 코드가 바뀐 경우만
- `PLAN = (("optimize", "all"),)` — 공통화는 자동 안 함
- `devwork`: Claude Code로 수정, `DENY_RULES` 경로 금지, 테스트 통과 필수(원래 실패하던 테스트는 허용)

## 9. 회의 (`meeting.py` · `planning.py`)

- `MODES`: all(회의실 A) · leaders(회의실 B) · boss(사장실, `BOSS_MAX = 5`) · plan/mkt/design/dev(팀 책상, `TEAM_MODES` 동시 가능)
- `MODE_TASK` = meeting / meeting_leaders / meeting_boss / meeting_{team}. 주제 없으면 `TEAM_TOPIC`
- Claude 1회 호출, 아이디어마다 action id (research · write · issue · search · polish · analyze · review · push · deploy · optimize · common · meeting · leaders · summon)
- `FOCUS`: all · money · feature · content · growth
- 저장: `planning.save` → `data/proposals/`

## 10. 마케팅 · 이미지 · YouTube

- 검색 개선(`search_fix`): okadmin `gsc_seo_worker.run_seo_jobs` · `write_gsc_seo_run`(8090과 같은 기록) · `_SEO_LOCK` · 같은 글 `SEO_MAX_TRIES = 4`
- 다듬기: `POLISH_MAX_TRIES = 4` · 목록 150개
- 분석: GA4·GSC 28일, `refresh_targets`로 저CTR·저노출 글을 to_fix에
- 이미지
  - `placepick.MAX_CANDIDATES = 3` (가장 잘 맞는 장소 1곳)
  - `gcsimage`: okadmin `_upload_payload_to_gcs` 방식, `Cache-Control: no-cache`, `bump_site_thumbnail_cache`, 바뀐 사진만 sync (`workers._gcs_push`, `_gcs_push_changed`)
  - `imagecleanup`: 보수적 미사용 탐지, 백업 `data/image_backups/cleanup/`
  - AI 그리기: Gemini/Imagen (`redraw`)
- YouTube: `hq.youtube.enabled` (okramen · jpfun · okcaddie · okonsen) · API `/site/{id}/youtube/api/…` · 키 `YOUTUBE_API_KEY` · 후보 검색 `costs.take("youtube")` · 캐시 `data/youtube/{site}_candidates.json` · `/youtube` → `/images#youtube` 리다이렉트

## 11. 비용 · 어필리 · 사용량 (`app/money/`)

- `costs.DEFAULT_LIMITS`: daily_images 10 · daily_places 30 · daily_youtube 80 · monthly_yen 3000. `take()`가 넘으면 `OverLimit`
- 비용 카드 `_costs_card.html`: GCP 금액 · 표 · `mp-meters`(Claude 창 · 오늘 한도 `cap_rows()` · 그림·사진 월 ¥). `?fresh=1`로 다시 읽기, 10분 넘으면 stale
- 어필리 `_affiliate_card.html`: 楽天·Agoda·Amazon (A8은 UI 숨김) · 0은 `.zero` · 로그인 필요는 `.mc-foot.alert`
- `affsync`: 브라우저 프로필로 楽天·Agoda·Amazon 대시보드를 몇 시간마다 읽음 (A8 중단)
- `usage`: `/usage/track` → `data/hq_usage.json`
- CSS: `admin.css` 끝 「어필리 · 비용 v2」 블록 (`.mp-top` · `.mp-hero` · `.mp-meter` · `.mp-bar`)

## 12. 사장 로그인 · 도구

- Google OAuth (okadmin 클라이언트 재사용), 허용 이메일 `data/boss.json`
- `BOSS_BLOCKED = (review, deploy, push, merge)`
- 도구 점검: `tools/check` · `tools/claude-login` · `tools/restart`

## 13. 개발 규칙

- 버전 관리: git `https://github.com/starful/starful-hq` (main). 작업 전 백업 대신 커밋. `data/` · `.bak/` · `__pycache__/` · `.env`는 추적 안 함 (`.gitignore`)
- 자동 진행(PR·배포) 대상이 아님 — HQ는 Mac 로컬에서만 돎
- 서버 재시작 = 돌던 잡 끊김. 큰 수정은 잡이 없을 때
- 자동 생성 파일(.pyc 등)은 변경으로 치지 않음
- 화면은 자동 리로드하지 않음: 지도·「지금 하는 일」은 폴링, 서버 HTML은 「끝난 일이 있어요 · 화면 새로고침」배너만(짧은 잡 연속 완료에 토스트 없음)
- 사이트 저장소의 사장님 미커밋 작업은 PR로 정리한 뒤 Mac 저장소 동기화 (git lock 파일 삭제에 /opt/work 삭제 권한 필요)
- 화면 변경: Claude Design 캔버스 → HQ 코드 → 기획서 11장 → 이 문서(구현 내용)

## 14. 테스트 · 알려진 문제

- 테스트: `pytest` 17개 파일 · 104개 (2026-10-30 Mac에서 전부 통과)
- VM(Cowork)에서 실행하면 81개 실패 (404 · KeyError — sites.yaml 경로 등 환경 문제로 보임, 확인 필요)
- `test_snapshot` 검색 테스트가 데이터에 의존
- 서버 재시작으로 끊긴 잡 실패 많음(「잡이 끊김」 79건) → 재시작 후 자동 재담기 검토
- 「회의 결과 파일이 없어요」 18건 원인 미확인
- 반복 실패: 「리뷰에 적힌 파일을 PR에서 찾지 못했어요」 · `.gitignore` 되돌림 · 미커밋 `_data.json`
