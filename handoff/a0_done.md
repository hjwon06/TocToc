# A0 인프라 에이전트 핸드오프 문서

## 완료일: 2026-03-18

---

## 1. DB 모델 목록

### Receipt (`app/models.py`)
| 필드 | 타입 | 설명 |
|------|------|------|
| id | Integer, PK, autoincrement | 기본 키 |
| image_path | String(500), NOT NULL | 저장된 이미지 상대 경로 |
| receipt_date | Date, nullable | 영수증 날짜 (OCR 추출) |
| amount | Integer, nullable | 금액 (원 단위) |
| is_manual | Boolean, default=False | 수동 입력 여부 |
| ocr_raw | Text, nullable | OCR 원본 텍스트 |
| created_at | DateTime(tz), server_default=now() | 생성 시각 |
| updated_at | DateTime(tz), server_default=now(), onupdate=now() | 수정 시각 |

**인덱스**: `idx_receipts_date` (receipt_date), `idx_receipts_created` (created_at)

---

## 2. 스킬 목록

### upload_skill (`skills/upload_skill.py`)
```python
# 파일 저장
from skills.upload_skill import save_upload
path = await save_upload(file=upload_file, upload_dir=settings.UPLOAD_DIR)

# 파일 크기 검증
from skills.upload_skill import validate_file_size
is_ok = await validate_file_size(file=upload_file, max_size_mb=settings.MAX_FILE_SIZE_MB)
```
- 허용 확장자: jpg, jpeg, png, heic
- UUID 기반 파일명 자동 생성
- try/except 내장

---

## 3. 환경변수 목록 (`.env`)
| 변수 | 기본값 | 설명 |
|------|--------|------|
| DATABASE_URL | postgresql+asyncpg://postgres:password@localhost:5432/toctoc | DB 접속 URL |
| ANTHROPIC_API_KEY | (필수) | Claude Vision API 키 |
| UPLOAD_DIR | static/uploads | 업로드 파일 저장 경로 |
| MAX_FILE_SIZE_MB | 10 | 최대 업로드 크기 (MB) |
| APP_HOST | 0.0.0.0 | 서버 호스트 |
| APP_PORT | 8000 | 서버 포트 |

---

## 4. 인프라 구성
- **DB**: PostgreSQL 16 (docker-compose.yml)
- **Alembic**: 비동기 마이그레이션 설정 완료 (`alembic/env.py`)
- **FastAPI**: lifespan에서 개발용 테이블 자동 생성
- **CORS**: localhost:8000 허용

---

## 5. 다음 에이전트 가이드

### A3 (AI/OCR 에이전트)
- `app/services/ocr.py`부터 시작
- `ANTHROPIC_API_KEY`는 `app/config.py`의 `settings.ANTHROPIC_API_KEY`로 접근
- OCR 결과는 `Receipt.ocr_raw`에 저장, 파싱된 날짜/금액은 `receipt_date`/`amount`에 저장
- GPT/Claude 호출 시 반드시 try/except + fallback 포함

### A4 (비즈니스 에이전트)
- `app/routers/receipts.py`부터 시작
- DB 세션: `from app.database import get_db` → `Depends(get_db)`
- 파일 업로드: `from skills.upload_skill import save_upload, validate_file_size`
- 템플릿: `app/templates/` 디렉토리 사용 (Jinja2)
- main.py에서 라우터 include 주석 해제 필요
- Receipt 모델: `from app.models import Receipt`

---

## 6. 실행 방법
```bash
# Docker로 실행
docker compose up -d

# 로컬 개발
pip install -r requirements.txt
docker compose up -d postgres   # DB만 실행
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# 마이그레이션
alembic revision --autogenerate -m "init"
alembic upgrade head

# 테스트
pytest tests/ -v
```
