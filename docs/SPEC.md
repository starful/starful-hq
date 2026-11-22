# Starful HQ 개발 사양 (SPEC)

> 비즈니스 요건(목적·대상·업무 흐름·화면 요구·규칙의 이유·결정)은 기획서 `Google Drive/내 드라이브/AI/Starful HQ/Starful HQ 기획서.html`.
> 화면 설계는 Claude Design 캔버스 「Starful HQ 화면 설계」(https://claude.ai/artifact/4euhfrt6Xw6yUAfQB8Dwu2), 기획서 11장에도 같은 화면.
> 이 문서는 개발 사양만: 실행 환경 · 코드 구조 · 데이터 · 라우트 · 상수 · 구현 규칙 · 테스트 · 알려진 문제.

2026-11-04 기획서에서 분리 · @스타풀
2026-11-21 SEO 안전(검사·동결·노출 감시) · 인수인계 메모 · GSC 일별 그래프 추가 (okonsen·okcaddie 검색 노출 급락 대응)

## 1. 실행 환경

- Mac 로컬 `/opt/work/starful-hq`, 포트 8091 (http://127.0.0.1:8091)
- 기동: 로그인 시 LaunchAgent `com.starful.hq` (상시) · `./hq-ctl.sh on|off|status` · 수동 `./start.sh`. Flask stat 리로더라 `.py`를 고치면 서버가 재시작되고 돌던 잡은 끊김. 템플릿은 `TEMPLATES_AUTO_RELOAD`로 재시작 없이 반영
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
    office/            __init__(배치·팀·자리·B1 TF) · jobs · meeting · boss
    work/              workqueue · autoship · autodev · devwork · workers · actions · planning · marketing · seo_guard · handoff
    content/           contentkit · jpcontent · cdbcontent · poicontent · placepick · gcsimage · imagecleanup · youtube_api · youtube_queue
    money/             costs · affiliate · affsync · usage
    partners/          제휴 공개 메일 (사이트별 JSON · campus 공유 school_emails)
    templates/         site.html · home.html · partners_schools.html · _*.html 조각 (_office_map · _money_card · _affiliate_card · _costs_card …)
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
| jpfun | datalab | 60 | poi |
| krcare | datalab | 50 | "" |
| sitekpi | basement | 10 | "" |

### 3-1. contentkit (`app/content/contentkit.py`)

content 타입별 쓰기·리서치·slug·레이아웃. UI seed/topics는 kit의 `seed_hint`/`topics_hint` → `cur_site.words`.
CompanyDB: 마케 분석 `topics`는 상장사 dict만(`normalize_analysis_topics`). `ensure_analysis_picks`는 티커·미보유만 맡길 목록에 넣고, 가이드 문자열·이미 있는 회사 분석 픽은 제거.
OKPy: 맡길 목록에 분석 새글 추천을 넣지 않음(`ensure_analysis_picks`가 analysis 픽 제거). UI는 `pick_groups_for_write`로 `OKPY_CATEGORIES`별 리서치 픽(빈 칸 포함). **BA**는 영문 쓰기 그룹(`Business Analysis (영문)`)과 일본어화 칸(`ba_ja_pending` · 액션 `ba_ja`)을 분리. 기획 키워드(시드 칩)는 **일본어만** · `OKPY_CATEGORY_SEEDS`로 허용 카테고리별 라운드로빈 (`seed_options`/`seed_chips`). 리서치는 카테고리당 최소 2픽(`OKPY_MIN_PER_CATEGORY`) · 부족분은 `_okpy_fill_category_gaps`로 채움.
CompanyDB 대기열 `queue_picks`는 KR·US·JP 라운드로빈(JP 몰림·EDINET 동시 스캔 완화). JP 빌드는 `_JP_BUILD_LOCK`. `write_picks`는 실패 사유를 한 줄에 남기고 큐 픽을 research에 upsert.
CompanyDB JP: `write_picks` 시작 시 `edinet_ready_tickers`로 유가증권보고서 일괄 확인 → 없는 티커는 빌드 전에 failed.
공시 없음(EDINET 유보·DART 없음)은 `mark_skipped` → `data/universe_skip.json`에 기록하고 `drop_from_queue`로 `universe_extra`에서도 제거. 찾기·맡길 목록·분석 픽·enqueue는 skip 티커를 제외. KR 빌드는 FY2024 없으면 2023 재시도.
머지 후 feat 브랜치에 로컬 dirty가 남으면 `ensure_on_main_if_idle`이 stash로 main 복귀(`merge_pr`도 동일). autoship은 merged+dirty면 배포 대기 대신 다음 PR(`issue`)을 먼저 탐 — 「올릴 파일 N개」와 「머지됨 · 올릴 차례」에 동시에 묶여 멈추던 순환 방지.
사장님 = 승인만(조사·입력·기술 값은 사원/에이전트 제안 후 맡기기·확인).
CompanyDB는 `can_research: false` · 직접 입력 없음. 기획 상세 **회사 찾기 맡기기** → `queue` 잡(`find_and_queue_companies` → `enqueue_ticker`). 찾기 비율 **JP 60% · KR 20% · US 20%** (`market_quota` · `fill_market_gaps`). 말풍선은 「추가할 회사 고르는 중」(키워드 N개 문구 없음). `queue` 잡 종료 시 autoship.kick 생략(대기열 파일만 dirty라 PR 안 탐) · 페이지 추가하기(write) 뒤에 PR.
맡길 목록 = 대기열 + 분석 → 회사 추가하기.
기획 상세 `.write-flow`: research/write 잡이 이 사이트에서 끝나면 전체 리로드 없이 `/write` HTML로 맡길 목록만 교체(`scheduleWriteRefresh`). 실패 시에만 「화면 새로고침」배너.

| content | 구현 | 하는 일 |
|---|---|---|
| okpy_posts | okpy `generate_posts` | okadmin 트렌드·토픽 뱅크(`BANK_TO_CATEGORY`) → 리서치 → 카테고리별 글. **추가 가능 카테고리** `contentkit.OKPY_CATEGORIES`: python · cloud · terraform · dev-method · data-analysis · eng-comms · ai-models · career. 아카이브만(신규 추천·쓰기 안 함): data-model · pmbok · agile-scrum · fit-journey. **data-analysis(BA)** 초안은 영어(`lang: en` · `ja_pending`) → 기획 액션 `ba_ja` / 잡 `translate_ba_ja`로 일본어화(쓰기와 분리) |
| campus_guides | `content/jpcontent.py` | 학교 가이드 · 번역 · Gemini/Imagen 커버 |
| companydb | `content/cdbcontent.py` | EDINET/DART/SEC 공시 → 기업 페이지 · 로고 |
| poi | `content/poicontent.py` | Google Places 조회 → Kit 필드 (okramen·okcaddie·okonsen·jpfun). `resolve_category`: 비교·ガイド·アクセス 등 가이드형 제목은 장소(noun)로 쓰지 않고 `가이드`로 강제 (회의 category 누락·기본값 cats[0] 오분류 방지) |
| "" | — | 없음 (krcare · sitekpi) |

## 4. 데이터 (`data/`)

| 파일 | 내용 |
|---|---|
| `sites/{id}/work_queue.json` | 실행 큐 |
| `sites/{id}/prefs.json` | `queue_paused` |
| `sites/{id}/research.json` | 리서치 결과 (`_RESEARCH_LOCK`) |
| `autoship.json` | `offices.<office>.on` (층 단위 토글, **없으면 꺼짐**) · 사이트별 `checked_at` |
| `wip_baseline.json` | 사장님 미커밋 파일 기준 (처음 본 시점) |
| `proposals/{id}.json/.md` | 회의 결과. 층 전체 회의는 `offices/{office}/proposals` |
| `spend_limits.json` | 하루·월 한도 (현재 이미지 10 · Places 1000 · 월 ¥1000 · YouTube 80) |
| `hq_usage.json` | HQ 화면 클릭 사용량 |
| `boss.json` | 사장 로그인 허용 이메일 |
| `home.json` 등 | 홈 스냅샷 (새로고침·작업 종료 후 다시 셈) |
| `image_backups/cleanup/` | 이미지 정리 백업 |
| `youtube/{site}_candidates.json` | YouTube 후보 캐시 |
| `partners/school_emails.json` | JP·KR Campus 어학원 (`schools[]`) · `last_collect_at` · 항목 `collected_at`/`collect_tried_at` |
| `partners/{okonsen,okcaddie,jpfun,krcare}.json` | 온천·골프·레저·클리닉 (`items[]`) · 동일 수집 시각 필드 |
| `followups.md` | 리뷰 후속 과제 |
| `seo_freeze.json` | 콘텐츠 동결 `{site: {until, reason, since}}` · 기한 지나면 자동 해제 · 기존 기한보다 짧아지지 않음 |
| `seo_watch.json` | 노출 감시 마지막 결과 `{checked, sites: {id: {now, before}}, alerts}` (하루 1번) |
| `seo_alerts.json` | 노출 급락 기록 `{site: {text, at, first_at, acked}}` (지금은 화면에 안 띄움) |
| `seo_guard.json` | (선택) SEO 안전 한도 덮어쓰기 — 없으면 `seo_guard.DEFAULTS` |
| `office_moves.json` | 임시 층 이동·소등 `{closed: [office], moves: {person: {office, seat}}, note}`. 2026-11-22 2층 소등 · 2층 사원 8명 1층 근무(검색 복구 기간). 비우면 원래대로 |

## 5. 라우트

| 영역 | GET / POST |
|---|---|
| 공통 | `/` · `/office` · `/job` · `/staff` · `/staff/hire` · `/staff/fire` · `/staff/approve` · `/staff/reject` · `/usage` · `/usage/track` · `/healthz` · `/login` · `/oauth/callback` · `/logout` · `/boss/move` · `/refresh` · `/site-icon/{sid}` · `/partners/schools`(→ JP/KR 현황) |
| 이용자 | `/site/<site>/users` · `/site/<site>/sitekpi-users`(별칭) · `/site/<site>/urls`(URL별 숫자) |
| 현황·회의 | `/site/{id}` · `/meetings` · `meeting` · `meeting/top` · `meeting/run` · `propose` · `proposal/{pid}.md` · `autoship` · `/office/{office}/autoship` · `/office/{office}/partners/collect` POST(층 메뉴 메일 수집) · `queue/pause` · `partners`(제휴 메일 팝업) · `partners/collect` · `partners/collect-floor` |
| 기획 | `write` · `write/research` · `write/picks` · `write/new` |
| 마케 | `search` · `search/analyze` · `search/clear` · `polish` · `analysis/{aid}.md` · `analytics/card` |
| 디자인 | `images` · `images/covers` · `logos` · `places` · `candidates` · `candidate` · `pick` · `redraw` · `sync` · `cleanup` · `file/{name}` · `youtube`(→`images#youtube`) · `youtube/api/…` |
| 개발 | `ship` · `ship/issue` · `review` · `fix` · `push` · `merge` · `refactor` · `auto` |
| 카드·도구 | `costs` · `costs/limits` · `affiliate(/{what})` · `tools/check` · `tools/claude-login` · `tools/restart` · `run/{task}` · `action/{aid}` · `panel/{team}` |

- 사이트 라우트는 `/site/{id}/…`. `_OPEN_BEFORE_CONNECT = {site_status, site_costs_card, site_affiliate_card}`
- 탭: `TEAM_TABS` = status/plan(write)/mkt(search, polish 포함)/design(images)/dev(ship). `STEP_TAB["meet"] = ""` (회의는 탭 아님)
- `office.is_borrowed()`이면 `site_meeting`은 `_back("status")` (기본 B1 TF는 borrow 아님 → 회의 가능)

## 6. 오피스 · 잡 (`app/office/`)

- `STAFF = {"datalab": _STAFF_1F, "playground": _STAFF_2F, "basement": _STAFF_B1}`
- 고정 사원 **16명** = 리더 4(1층) + 사원 12. B1 TF 임시 **4** (`tf_rio`·`tf_mina`·`tf_noa`·`tf_jun`, `temp: true`, lead 없음). 합계 20. 리더 회의·사장실 = `company_leaders()`. 알바 3 = 휴가 대행만(맥스·명단 밖).
- 채용·해고 `app/office/staffing.py` · `data/staffing.json`: 맥스 `MAX_STAFF=25`. 사장님이 **층·팀**을 고른 뒤 「공고 올리기」→ 후보 확인/거절. 정원 여유(`open`)만 있으면 해고 없이도 채용. 해고 빈자리가 같은 팀이면 그 자리 우선. 리더 해고는 **같은 팀 후임** 필수(`leads` 맵). `POST /staff/hire|fire|approve|reject`. 반영 후 `rebuild_staff_caches()`.
- 임시 층 이동·소등 (`staffing.office_moves()` · `data/office_moves.json`): `moves`의 사원은 `_base_people()`에서 `office`·`seat`이 바뀜(`moved: True`). `closed` 층은 지도가 어두워지고 「🌙 소등 중 · 사원은 N층에서 근무」(`office._lights_off`, `.lights-off` · `.lights-off-veil`). 사원 없는 층은 일을 맡을 사람이 없어 시작 안 됨 · `_next_up`은 빈 팀을 건너뜀. 2026-11-22 1층 팀별 4명(추가 자리: 기획·마케 책상 3번째, 디자인 책상 아래 2, 개발 블록 3번째·아래 줄 3)
- 리더는 1·2·B1을 오간다: 다른 층 잡/회의면 그 층 지도에 방문(`visitor`), 집 층에서는 `away`.
- 스케줄 `app/office/schedule.py` · `data/office_schedule.json`: 근무 시(기본 월–일 **08–22**) · 점심 · 휴가 · 알바 3명 대행. **주말 휴무 없음**. 팀당 동시 휴가 1명. `enforce_hours`면 퇴근 후 새 잡 거부. 퇴근·휴가는 지도에서 숨김. 퇴근이면 지도 **위** `hours_notice` + 팀 책상판에 날짜별 재미 문구(`team_off_quip`). 휴가면 `vacation_notice`. (CS팀은 이후)
- `TASK_TEAM`: plan = research · write · **ba_ja** · issue · propose / mkt = search · polish · analyze · partner_collect · fix / design = images / dev = fix_code · refactor* · optimize · review · push · deploy. 새 좌석 업무 액션 `_add(..., team in TEAMS, task=…)` 는 반드시 `TASK_TEAM`에도 같은 팀으로 등록 (`actions._add`가 import 시 검사 · `meeting*` 제외). 빠지면 `ordered_members`가 빈 리스트 → 예전엔 「모두 일하는 중」으로 오인. 지금은 `start_task`가 `TASK_TEAM` 누락을 명시. 회귀: `tests/test_office.py` (`test_every_seat_work_action_task_is_in_task_team`)
- 제휴 메일 수집: 층 메뉴(자동 진행 아래)「N층 메일 수집 맡기기」만 층 일괄. 현황은「메일 목록 보기」·팝업「이 사이트만 수집」. 태스크 `partner_collect_floor` / `partner_collect`. 흐름: 콘텐츠 URL → Places → http URL만 수집. 사이트 단독 최대 20 · 층 일괄은 Places 예산 24를 사이트 수로 나눔. 사이트: jpcampus·krcampus·okonsen·okcaddie·jpfun·krcare
- `TASK_GROUP` (사이트당 한 명): refactor 그룹(refactor·refactor_front·refactor_back·optimize) · pr 그룹(issue·review·fix·fix_code·push·merge) · deploy 그룹
- `ITEM_TASKS = (write, search, polish)` · `OPTIONAL_TASKS = (write, search)` · `AWAY_TASKS = (research, review, propose, analyze)` → 프리석/핫데스크
- `jobs.py`: 사원 1명당 잡 1개 · `MAX_CONCURRENT = NEXT_MAX_JOBS(기본 6)` · 저장소별 git 잠금(`work.git_turn()`)
  - `WIP_TASKS = (issue, push, deploy, optimize, refactor, refactor_front, refactor_back)` → `snapshot.owner_wip()`가 있으면 시작 안 함
  - `CONTENT_TASKS = (research, write, images)` → kit의 `can_write` 필요
- B1 Task Force: `_LAYOUT_B1` (`borrow: False`, TF 탁자 meeting/leaders/meet3, 핫데스크 3, plates에 plan 포함) · sitekpi 전용. 옛 borrow 헬퍼(`is_borrowed` · `_build_borrowed` · `BORROW_TEAM`)는 플래그용으로 유지. 테스트 `tests/test_b1.py`
- 「현황」`ss-actions` **이용자 보기** (SiteKPI는 **이용자 · 설문 보기**). 요약 블록 없음 · 클릭 시 `GET /site/<site>/users?days=7|30|90[&fresh=1]` (`/sitekpi-users` 별칭). `app/site_users.py`:
  - SiteKPI → `sitekpi_users` (visitlog+설문)
  - `hq.visitlog: true` (okpy 외 9곳 live · krcare 포함) → `visitlog_users`가 `https://{domain}/hq/api/users` Bearer(HMAC `visitlog-hq-api-v1:{site}` from Secret `VISITLOG_SECRET`) · 방문자 표→오른쪽 흐름(설문 없음)
  - 그 외 → `ga4_users` (GA4 요약 폴백)
  - 「URL별 숫자」 탭: `GET /site/<site>/urls?days=7|30|90[&fresh=1]` → `_url_stats.html`. `app/url_stats.py`가 GA4 `fetch_ga4_pages`(조회·사용자·세션) + GSC `fetch_gsc_pages`(클릭·노출·CTR·순위, 노출 상위 500 URL)를 경로(쿼리·끝 `/` 제외)로 합침. 첫 열은 GA4 `pageTitle`(사이트 이름 꼬리 뺌, 없으면 경로) 한 줄 말줄임. 조회수순 최대 500행 · 열 머리 정렬 · 캐시 5분. 테스트 `tests/test_url_stats.py`
  - dialog `_site_users.html` · `static/sitekpi_users.css`. 키트: `/opt/work/_shared/visitkit` → 사이트 repo에 vendor(`okpy/visitkit` · `visit_track.js`). 테스트 `tests/test_site_users.py` · `tests/test_visitkit_summarize.py` · okpy `tests/test_visitkit.py`
- 지도: 6:5 비율, `nav-*.json` 격자 위를 걷기, 바닥 SVG + 앞쪽 SVG 두 겹

## 7. 실행 큐 (`app/work/workqueue.py`)

- 상태: queued → running → done / failed / held
- `LOOP_SEC = 60` · `KICK_DELAY = 4.0` (잡 종료 후 다음 시작)
- held: 열린 PR이 막는 제안(feature · search · polish) → 머지 후 queued (`actions.PR_HOLD_TASKS`)
- `sweep_zombies`: 재시작으로 끊긴 running → failed(「잡이 끊김 · 다시 담아 주세요」)
- 검색 백로그 항상 켬 · 회의 액션 일괄 담기 `MAX_BATCH = 15` (`actions.py`, `routes.py`)

## 8. 자동 진행 (`autoship.py` · `workers.py`)

- `LOOP_SEC = 180` · `KICK_DELAY = 4.0`. 꺼진 층·동결 사이트(`seo_guard.frozen`)는 스킵. 층 설정이 없으면 꺼짐(2026-11-21부터)
- 루프 시작 1회 `handoff.scan_all()` · 매 루프 `seo_guard.watch_traffic()`(하루 1번만 실제 점검)
- stage: changes → pr → (고치기) → deploy → check / stuck / idle. UI: 현황 `ss-actions` 한 줄(회의 결과 · 올렸어요·시각 · 사이트 열기). 확인 버튼 없음. 개발 탭·상세에 반영 블록 없음
- 층 자동 진행 토글: 헤더 층 선택 메뉴(`_site_switch.html` · `POST /office/<id>/autoship`)만. 팀 탭·상세에는 없음
- changes: `issue_pr` (okadmin `github_ops.ship_prepare`)
- pr: `review_and_merge`. 리뷰는 Claude가 「판정: / 요약 / 필수 / 제안」 줄 → `split_review`
  - `_is_none`: 「필수: 없음 (설명)」은 없음으로 · `_is_resolved_note`: 「이전 필수…해결됨」 제외
  - 고치기 입구 `start_fix_for_review`: 코드 필수 → `devwork.fix_review`(fix_code) · 글만 → `fix_from_review`(fix)
  - `fix_review` / `fix_from_review` / `push_fixes`는 `ensure_pr_branch`로 PR 브랜치로 맞춘 뒤 작업(브랜치 불일치로 바로 실패하지 않음)
  - `sync_merged_pr_from_github`: GitHub에 이미 머지됐는데 `flow.merged=false`면 autoship·고치기·stage에서 동기화해 같은 실패 루프를 끊음. `pr=null`인데 `merged=false`인 낡은 flow도 정리
  - `ensure_on_main_if_idle`: 열린 PR이 없으면 로컬 HEAD를 main으로 되돌림(머지 후 feat에 남아 「main에서만 올려요」로 배포가 며칠씩 막히던 것 방지)
  - 머지 충돌: `gh pr update-branch` 한 번 시도 후 재머지. 그래도 안 되면 `flow.merge_conflict=true` · autoship 재시도 중지 · stage `stuck`(사람). 수동 머지·해결 후 플래그 해제
  - `MAX_FIX_ROUNDS = 2` 후에도 필수가 남으면 **머지하지 않고 멈춤**(사람 확인). 남은 건 `followups.md`. 변경 없는 push도 1회로 셈
  - 에이전트 금지 경로만 남으면 `needs_person`으로 멈춤(`review_round`를 한도로 올려 autoship이 다시 넘기지 않음). 후속으로 남기고 머지하던 경로(`merge_followup`)는 2026-11-21 폐기
  - `merge_pr`는 머지 전에 `seo_guard.check_branch` + 남은 필수 확인 → 막히면 `flow.seo_blockers`에 적고 실패(8-2)
  - GitHub에서 이미 머지됐으면 로컬 pull 실패와 무관하게 `flow.merged = True`
- deploy (`workers.deploy`): `owner_wip()` 있으면 중단 · main 브랜치만(`is_production_branch`) · okadmin `start_deploy(mode="deploy-only")` + `wait_for_deploy_job(timeout=40분)` · 성공 시 `write_flow(deployed=True, deployed_at)`
- check: 배포 후 현황에 「올렸어요 · 시각」표시만 (`needs_person=False`). `checked` POST·확인 버튼 폐기

### 8-1. 자동 최적화 (`autodev.py`)

- `CHECK_SEC = 15분` · 첫 확인 3분 · `MIN_GAP = 24h` · 코드가 바뀐 경우만
- `PLAN = (("optimize", "all"),)` — 공통화는 자동 안 함
- `devwork`: Claude Code로 수정, `DENY_RULES` 경로 금지, 테스트 통과 필수(원래 실패하던 테스트는 허용)

### 8-2. SEO 안전 (`seo_guard.py`)

2026-11-21 추가. 10/27 날짜만 바뀐 페이지·제목 대량 재작성 PR이 머지된 뒤 okonsen·okcaddie 검색 노출이 0 가까이로 떨어진 데 대한 대응.

- `check_diff(git, base, head)` / `check_branch(git, branch)` → 막는 이유 목록. HQ에서 하는 **모든 머지**(자동·버튼) 전에 `merge_pr`가 부름. 막히면 GitHub에서 사람이 직접 머지해야 함
  - DB 파일(`*.sqlite3` · `*.db`) 추가
  - Indexing API 호출(`indexing.googleapis.com` · `urlNotifications`) 추가
  - 기존 페이지(`.md/.markdown/.mdx`) 수정 > `max_existing_pages`(5)
  - 본문은 그대로이고 날짜 키(`DATE_KEYS`)만 바뀐 페이지 1개 이상
  - 본문 변경 없이 날짜를 뒤로 민 페이지 1개 이상
  - `title`·`seo_title`·`seo_description`·`description` 바뀐 기존 페이지 > `max_title_changes`(3)
  - 동결 사이트의 콘텐츠 변경
- 본문 비교는 front matter를 뗀 뒤 공백을 하나로 접어 비교(빈 줄 추가만으로는 본문 변경 아님)
- 동결: `frozen(site)` · `freeze(site, reason, days)` (`freeze_days` 30, 기존 기한보다 짧게 안 함). 동결 사이트는 autoship 스킵 + 콘텐츠 PR 머지 막음 + 페이지를 고치는 일 시작 거부(`jobs.FROZEN_TASKS` = write · polish · search · fix · images · ba_ja, `start_task`·`start_task_split` 둘 다). 코드 작업(refactor·optimize·fix_code·deploy)은 허용. 2026-11-21 okonsen·okcaddie·okramen을 2026-12-31까지 동결
- 노출 감시 `watch_traffic()`: 하루 1번, 사이트마다 okadmin `fetch_gsc_pages`로 노출 합. GSC 최신 `lag_days`(3)일은 아직 채워지는 중이라 빼고, 그 앞 7일 하루 평균 < 그전 28일 하루 평균 × `drop_ratio`(0.6)이고 그전 평균 ≥ `min_daily_impressions`(50)이면 (11/22 첫 버전은 최신 며칠을 그대로 넣어 jpcampus·krcampus를 잘못 동결 → 고침) 동결 + 그 층 autoship 끔 + 이벤트(「SEO 감시 · 경보」) + `seo_alerts.json`. GSC 데이터는 2~3일 늦으므로 그만큼 늦게 잡힘
- 화면 배너·Mac 알림은 넣었다가 사장님 요청으로 뺌(`_seo_alert.html`은 빈 파일, `notify_mac`은 호출 없음). 확인은 현황 탭 GSC 그래프로
- 사이트 저장소 쪽: okcaddie `request_indexing.py` · krcampus `scripts/gsc_resubmit.py` · companydb `script/gsc_resubmit.py`는 `ALLOW_INDEXING_API=1`일 때만 Indexing API 호출(2026-11-21)

### 8-3. 인수인계 메모 (`handoff.py`)

- 잡이 실패하면(`jobs.start` except) `handoff.write(msg, task, who)` → 사이트 저장소 루트 `HQ_HANDOFF.md`: 시각·사이트·브랜치·작업·이유 + 커밋 안 된 파일 표(「⚠️ 날짜만 바뀜 — 되돌릴 것」 · 「날짜 바뀜」 · 「제목·설명 바뀜」 · 「본문 바뀜」 · 「새 페이지」). 한국어·영어 경고 포함
- `.git/info/exclude`에 등록해 커밋 안 됨. 잡 성공 후 작업 트리가 깨끗하면 `handoff.clear()`가 지움. HQ 시작 때 `scan_all()`이 모든 사이트를 한 번 훑음
- 이유: 10/27 끊긴 HQ 작업의 남은 변경을 다른 도구(Cursor)가 내용 모르고 PR로 올림

## 9. 회의 (`meeting.py` · `planning.py`)

- `MODES`: all(회의실 A) · leaders(회의실 B) · boss(사장실, `BOSS_MAX = 5`) · plan/mkt/design/dev(팀 책상, `TEAM_MODES` 동시 가능)
- B1 TF(`is_tf()`): UI·라벨·룸·팀 주제·역할 시점이 `_TF_*`로 갈림 (스탠드업/탁자, write/search/polish 액션 제외 · feature/analyze/none). **leaders·boss 금지** (`_TF_BLOCKED`) — 1·2층 사원을 부르지 않음. 참석 = basement PEOPLE만
- `MODE_TASK` = meeting / meeting_leaders / meeting_boss / meeting_{team}. 주제 없으면 `team_topic()` (`TEAM_TOPIC` 또는 `_TF_TEAM_TOPIC`)
- Claude 1회 호출, 아이디어마다 action id (research · write · issue · search · polish · analyze · review · push · deploy · optimize · common · meeting · leaders · summon)
- `focus_map()`: 1·2층 `FOCUS`(all · money · feature · content · growth) · B1 `TF_FOCUS`(all · money · ux · feature) — UI에 주제 칩
- 이미 있는 글: `existing_pages(site)` = posts 파일 이름(언어 `_en/_ko/_ja` 1개로, `guides/` 등 폴더 붙임, 최신순). `_existing_block`이 글을 쓸 수 있는 사이트마다 프롬프트에 넣음 (`데이터:` 14000자 자르기와 별도, 전체 `EXISTING_CHARS = 30000`, 사이트당 최소 3000자, 넘치면 오래된 것부터 뺌). write topics는 이 목록과 같거나 비슷하면 안 됨
- 저장: `planning.save` → `data/proposals/` (깨진 dated JSON은 `proposals.json` 같은 id로 heal). 회의 카드는 접지 않음. 회의 잡이 끝나면 `result_id`로 `/meetings?proposal=` 이동
- `OFFICE_MODES = ("all",)` 만 층 전체 저장. 리더·사장실·팀은 연 사이트 `proposals/`에 저장
- 사이트 브리핑 (`app/office/site_brief.py`, `_brief_block`): `_existing_block` 다음에 사이트마다 3개 블록. 하나라도 못 읽으면 그 블록만 빠지고, 브리핑 전체가 실패해도 회의는 그대로 진행
  - 기획서: Google Drive 동기화 폴더 `HQ_PLAN_DIR`(기본 `~/Library/CloudStorage/GoogleDrive-starful0418@gmail.com/내 드라이브/AI`)의 `<site id>/<site id> 기획서.html` (없으면 `<label>/<label> 기획서.html`). script·style·iframe(화면 설계)·nav를 빼고 블록마다 한 줄, 표는 `|`로. 사이트당 `PLAN_CHARS = 8000`자
  - 페이지별 성적: `url_stats.fetch(site_id, 30)` → 조회수 상위 10 + 노출 많고 CTR 낮은 10 (노출 `LOW_CTR_MIN_IMPRESSIONS = 50` 이상, 사이트 평균 CTR 미만, 노출 많은 순). 제목·경로·조회·클릭·노출·CTR·순위. 실패하면 생략
  - 최근 회의 결정과 결과: `planning.history` 중 최근 `RECENT_DAYS = 14`일 회의의 이 사이트 제안 + 실행 큐(`workqueue.list_items`, `pid`·`idx`로 연결) 상태(대기·실행 중·완료·실패·보류)와 실패·보류 이유. 회의 밖 실패 항목도 포함. 사이트당 `RECENT_MAX = 15`줄
  - 프롬프트 규칙: 이미 실패했거나 「이미 있는」 이유로 막힌 제안은 다시 내지 않음 · 노출 많고 CTR 낮은 페이지는 search 후보
  - 사이트 여러 개(전사 회의)는 병렬로 읽음 (최대 4). 테스트 `tests/test_site_brief.py`

## 10. 마케팅 · 이미지 · YouTube

- 검색 개선(`search_fix`): okadmin `gsc_seo_worker.run_seo_jobs` · `write_gsc_seo_run`(8090과 같은 기록) · `_SEO_LOCK` · 같은 글 `SEO_MAX_TRIES = 4`
- 다듬기: `POLISH_MAX_TRIES = 4` · 목록 150개
- 분석: GA4·GSC 28일, `refresh_targets`로 저CTR·저노출 글을 to_fix에
- 현황 트래픽 카드(`_analytics_card.html`): GA4 그래프 아래 **GSC 일별 그래프**(클릭 왼쪽 축 · 노출 오른쪽 축, `#hq-gsc-chart`) → 그 아래 GSC 검색어 표. 데이터는 `traffic_card()`의 `gsc.days`(okadmin `gsc_daily.rows`를 날짜·클릭·노출로 정리). 기간은 카드 기간과 같음
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

- 허용 이메일 `data/boss.json`. **로컬(127.0.0.1)에서는 원클릭 로그인** (Google 불필요). Google OAuth는 `HQ_BOSS_OAUTH=1` 또는 `/login?oauth=1` — redirect URI는 고정 `http://127.0.0.1:<PORT>/oauth/callback` (Cloud Console에 8091 URI 등록 필요; okadmin은 8090만 등록됨)
- 로그인해도 기능 제한 없음 (PR·머지·배포 포함). 사장 출석이면 쉬는 사원은 전원 자기 자리. **퇴근 시각**에 사장 로그인이면 헤더에 「근무 시간이 끝났어요 · 사장님도 퇴근해 주세요」
- 도구 점검: `tools/check` · `tools/claude-login` · `tools/restart`
- Claude 세션 한도(5시간/주간 ≥100%·level=over): `tools.halt_claude_limit` → 전 층 autoship 끔 + 전 사이트 실행 큐 pause · `data/claude_halt.json`. 잡 실패·autoship/workqueue 루프에서도 동일. 한도 중엔 층 메뉴 자동 진행 켜기 거부. 한도 풀린 뒤 사람이 다시 켬
- 다시 켜기: 현황 `ss-head`에 「⏸ 실행 큐 멈춤」칩 + 「실행 큐 다시 켜기」버튼(`POST /site/<site>/queue/pause` on=0 → `clear_claude_halt` + `set_paused(False)`). Claude 한도 중이면 버튼 숨김, 소등 층(`office.lights_off`)이면 칩·버튼 모두 숨김 (2026-11-22)

## 13. 개발 규칙

- 버전 관리: git `https://github.com/starful/starful-hq` (main). 작업 전 백업 대신 커밋. `data/` · `.bak/` · `__pycache__/` · `.env`는 추적 안 함 (`.gitignore`)
- 자동 진행(PR·배포) 대상이 아님 — HQ는 Mac 로컬에서만 돎
- 파일 이름은 무조건 영어: 영문자 · 숫자 · `-` `_` `.` 만 (한글·공백 금지). 폴더 이름도 같음. `tests/test_filenames.py`가 검사
- README.md는 기본 영어로 쓴다 (SPEC.md·기획서는 한국어)
- 서버 재시작 = 돌던 잡 끊김. 큰 수정은 잡이 없을 때
- 자동 생성 파일(.pyc 등)은 변경으로 치지 않음
- 화면은 자동 리로드하지 않음: 지도·「지금 하는 일」은 폴링, 서버 HTML은 「끝난 일이 있어요 · 화면 새로고침」배너만(짧은 잡 연속 완료에 토스트 없음)
- 사이트 저장소의 사장님 미커밋 작업은 PR로 정리한 뒤 Mac 저장소 동기화 (git lock 파일 삭제에 /opt/work 삭제 권한 필요)
- 화면 변경: Claude Design 캔버스 → HQ 코드 → 기획서 11장 → 이 문서(구현 내용)
- SEO 안전 규칙: `/opt/work/.cursor/rules/seo-safety.mdc` (Claude는 `/opt/work/.claude/CLAUDE.md`가 이 파일을 불러옴). 날짜·변경 규모·남의 변경·리뷰·Indexing API·사이트 구조·노출 필드·커밋 금지·동결

## 14. 테스트 · 알려진 문제

- 테스트: `pytest` 17개 파일 · 104개 (2026-10-30 Mac에서 전부 통과). 2026-11-21 `seo_guard`·`handoff`·리뷰/머지 변경은 pytest 미실행 — `seo_guard.check_diff`는 실제 PR(okonsen #44·okcaddie #46 막음, 일반 코드 PR 통과)로, `handoff.write`는 임시 저장소로 확인. 전용 테스트 추가 필요
- VM(Cowork)에서 실행하면 81개 실패 (404 · KeyError — sites.yaml 경로 등 환경 문제로 보임, 확인 필요)
- `test_snapshot` 검색 테스트가 데이터에 의존
- 서버 재시작으로 끊긴 잡 실패 많음(「잡이 끊김」 79건) → 재시작 후 자동 재담기 검토
- 「회의 결과 파일이 없어요」 18건 원인 미확인
- 반복 실패: 「리뷰에 적힌 파일을 PR에서 찾지 못했어요」 · `.gitignore` 되돌림 · 미커밋 `_data.json`
- (고침 2026-11-05) 「지금 브랜치가 PR 브랜치가 아니에요」 루프: `fix_review`가 checkout 없이 실패 + GitHub 머지 후 `flow.merged` 미동기화 → `ensure_pr_branch` · `sync_merged_pr_from_github`
