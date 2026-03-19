# toctoc — 프로젝트 규칙
# 글로벌 규칙: ~/.claude/CLAUDE.md 참조
# 프로젝트 지식: CONTEXT.md 참조

---

## 스택
- FastAPI + Jinja2 + HTMX + Alpine.js + Tailwind CSS
- PostgreSQL (AsyncSession only)
- Claude Vision API (OCR) — GPT-4o 아님
- Chart.js (월별 통계)
- Docker Compose + Coolify

## 에이전트 배치
```
A0 인프라   ✅ 완료 — models, config, database, alembic, skills, docker
A3 AI/OCR  ⬜ 다음 — services/ocr.py (Claude Vision)
A4 비즈니스 ⬜ 대기 — routers, templates, services
QA 검증    ⬜ 대기 — 통합 테스트, E2E
```

## 파일 소유권
```
A0: main.py, config.py, database.py, models.py, alembic/, skills/, docker-compose.yml, Dockerfile
A3: services/ocr.py, tests/test_ocr.py
A4: routers/receipts.py, routers/stats.py, services/image.py, templates/*, tests/test_receipts.py, tests/test_stats.py
QA: tests/integration/, tests/e2e/
```

## [NEVER] 프로젝트 금지
- Claude Vision 호출 try/except 없이 작성
- 동기 DB 세션 사용
- 다른 에이전트 소유 파일 직접 수정
- static/uploads/ 경로 하드코딩 (settings.UPLOAD_DIR 사용)

## [ALWAYS] 프로젝트 필수
- 영수증 금액은 원(₩) 단위 정수 저장
- OCR 실패 시 receipt_date=NULL, amount=NULL, ocr_raw에 에러 메시지 저장
- 목록 UI 페이지네이션 필수 (LEARNINGS #2)
- 이미지 확장자 검증은 upload_skill 경유

## UI 디자인 토큰
```
배경: off-white (#FAFAF8)
폰트: Pretendard
라운드: rounded-lg (max)
그림자: shadow-sm (max)
터치타겟: 48dp+
텍스트: 16px+
가로스크롤: 금지
고정높이: 금지
```

## 실행 명령어
```bash
# DB만 실행
docker compose up -d postgres

# 로컬 개발
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# 테스트
pytest tests/ -v

# 검수
ruff check app/ skills/ tests/
mypy app/ skills/ --ignore-missing-imports
```
