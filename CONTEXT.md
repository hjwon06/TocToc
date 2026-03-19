# toctoc (영수증 정리왕) CONTEXT
# 글로벌 규칙은 CLAUDE.md + REFERENCE.md 참조
# 자동 생성: 2026-03-18

## 프로젝트 개요
- **타입**: saas / **설명**: 식비 영수증 사진 → AI OCR → 날짜순 정렬 웹 서비스 / **타겟**: 개인/소규모 사업자

## 스택
- 백엔드: FastAPI + Jinja2 + HTMX + Tailwind CSS
- DB: PostgreSQL (AsyncSession)
- AI/OCR: Claude Vision API (고정 스택 GPT-4o 대신 스펙 지정)
- 차트: Chart.js
- 배포: Docker Compose + Coolify
- 파일 저장: 로컬 디스크 (추후 S3 전환 가능)

## 출격 구성
- **MCP**: Context7, Sequential Thinking, Serena, Playwright
- **스킬**: upload_skill.py
- **에이전트**: A0(인프라), A3(AI/OCR), A4(비즈니스), QA(검증)

## 파일 소유권
```
A0: main.py, config.py, database.py, models.py, alembic/, skills/, docker-compose.yml, Dockerfile
A3: services/ocr.py
A4: routers/receipts.py, routers/stats.py, services/image.py, templates/
QA: tests/
```

## UI 디자인 원칙
- 라이트 테마만, 따뜻한 off-white 배경
- 폰트: Pretendard
- shadow-sm, rounded-lg 최대
- 큰 터치 타겟 (48dp+), 큰 텍스트 (16px+)
- 가로 스크롤 없음, 고정 높이 없음

## DB 스키마 요약
(A0 완료 후 자동 기록)

## API 엔드포인트
- POST /api/receipts/upload — 영수증 업로드 (1장/여러장)
- GET /api/receipts?month=&sort= — 목록 조회 (페이지네이션 적용)
- PUT /api/receipts/{id} — 수정
- DELETE /api/receipts/{id} — 삭제
- GET /api/stats?month= — 월별 통계

## 검증된 패턴 (✅)
(개발 중 발견할 때마다 추가)

## 실패 패턴 (❌)
(개발 중 실패할 때마다 추가)

## 핸드오프 실제 기록
(각 Phase 완료 시 기록)
