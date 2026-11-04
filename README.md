# Starful HQ

사이트 관리용 로컬 백오피스. 가상 오피스에서 글·SEO·이미지·PR·배포를 한 흐름으로 처리합니다.

- 주소: http://127.0.0.1:8091
- 실행: `./start.sh` (또는 `./start.sh 7777`)
- 사이트 목록: `/opt/work/sites.yaml` 의 `hq:` 블록

## 폴더

```text
starful-hq/
  start.sh          ← 실행
  app/              ← 코드
    routes.py       ← 화면·API (Flask)
    sites.py        ← sites.yaml
    office/         ← 층·사원·회의
    work/           ← 맡기기·실행 큐·에이전트·배포
    content/        ← 글·이미지 (사이트 종류별)
    money/          ← 어필리·GCP 비용·HQ 클릭
    snapshot.py     ← 저장된 숫자
    templates/ static/
  data/             ← 런타임 (소스 아님)
  tests/
```

코드와 데이터는 분리되어 있습니다. `data/` 는 깃에 올리지 않는 로컬 상태입니다.
