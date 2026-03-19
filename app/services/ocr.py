"""Claude Vision OCR 서비스 — 영수증 이미지에서 날짜·금액·품목 추출.

A3 AI/OCR 에이전트 소유 파일.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import anthropic
from anthropic.types import ImageBlockParam, TextBlockParam

from app.config import settings

logger = logging.getLogger(__name__)

# ── 상수 ──────────────────────────────────────────────

SUPPORTED_VISION_TYPES: dict[str, str] = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
    # HEIC는 변환 후 JPEG로 전송
}

OCR_SYSTEM_PROMPT = (
    "당신은 한국어 식비 영수증 이미지에서 정보를 추출하는 전문가입니다. "
    "반드시 지정된 JSON 형식으로만 응답하세요. 추가 설명은 절대 하지 마세요."
)

OCR_USER_PROMPT = """이 영수증 이미지에서 정보를 추출해 아래 JSON 형식으로만 응답하세요.

```json
{
  "date": "YYYY-MM-DD 형식, 없으면 null",
  "amount": 총결제금액(숫자만, 원단위 정수), 없으면 null,
  "items": [{"name": "품목명", "price": 가격}],
  "store_name": "가게명, 없으면 null",
  "raw_text": "영수증에 보이는 전체 텍스트"
}
```

규칙:
- amount는 총 결제금액(합계)을 원 단위 정수로 입력 (콤마, "원" 제거)
- date는 반드시 YYYY-MM-DD 형식
- 영수증이 아닌 이미지라면 {"error": "영수증이 아닙니다"} 로만 응답
- JSON 외 다른 텍스트를 포함하지 마세요"""

# Claude Vision 모델
VISION_MODEL = "claude-sonnet-4-20250514"
# TODO(A0): VISION_MODEL을 config.py로 이동 검토
MAX_TOKENS = 2048


# ── 데이터 클래스 ─────────────────────────────────────


@dataclass
class OcrResult:
    """OCR 결과 데이터 클래스."""

    receipt_date: date | None = None
    amount: int | None = None
    store_name: str | None = None
    items: list[dict[str, object]] | None = None
    raw_text: str = ""
    success: bool = False


# ── 내부 함수 ─────────────────────────────────────────


def _get_client() -> anthropic.AsyncAnthropic:
    """AsyncAnthropic 클라이언트를 생성한다."""
    api_key = settings.ANTHROPIC_API_KEY
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY가 설정되지 않았습니다.")
    return anthropic.AsyncAnthropic(api_key=api_key)


def _load_and_encode_image(image_path: str) -> tuple[str, str]:
    """이미지 파일을 base64로 인코딩한다.

    Args:
        image_path: 이미지 파일 경로.

    Returns:
        (base64_data, media_type) 튜플.

    Raises:
        FileNotFoundError: 파일이 없을 때.
        ValueError: 지원하지 않는 확장자일 때.
        IOError: 파일 읽기/변환 실패 시.
    """
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"이미지 파일을 찾을 수 없습니다: {image_path}")

    ext = path.suffix.lstrip(".").lower()

    # HEIC 변환 시도
    if ext == "heic":
        try:
            import pillow_heif  # type: ignore[import-untyped]
            from PIL import Image
            import io

            pillow_heif.register_heif_opener()
            img = Image.open(path)
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=90)
            buffer.seek(0)
            b64 = base64.standard_b64encode(buffer.read()).decode("utf-8")
            return b64, "image/jpeg"
        except ImportError:
            logger.warning(
                "pillow-heif 미설치 — HEIC 변환 불가. "
                "원본 파일을 JPEG로 가정하여 전송합니다."
            )
            # pillow-heif 없으면 원본 바이너리를 그대로 전송 시도
            raw_bytes = path.read_bytes()
            b64 = base64.standard_b64encode(raw_bytes).decode("utf-8")
            return b64, "image/jpeg"
        except Exception as e:
            raise IOError(f"HEIC 변환 실패: {e}") from e

    # 일반 이미지
    media_type = SUPPORTED_VISION_TYPES.get(ext)
    if not media_type:
        raise ValueError(
            f"지원하지 않는 이미지 형식: .{ext} "
            f"(지원: {', '.join(sorted(SUPPORTED_VISION_TYPES.keys()))})"
        )

    raw_bytes = path.read_bytes()
    b64 = base64.standard_b64encode(raw_bytes).decode("utf-8")
    return b64, media_type


def _normalize_amount(raw: object) -> int | None:
    """금액 문자열을 원 단위 정수로 정규화한다.

    '12,500원' → 12500, '8500' → 8500, None → None.
    """
    if raw is None:
        return None

    text = str(raw).strip()
    if not text or text.lower() == "null":
        return None

    # 콤마, "원", 공백 제거
    text = text.replace(",", "").replace("원", "").replace(" ", "")

    # 소수점 처리
    try:
        value = float(text)
    except (ValueError, TypeError):
        return None

    amount = round(value)
    # 음수 또는 0이면 None
    if amount <= 0:
        return None
    return amount


def _normalize_date(raw: object) -> date | None:
    """날짜 문자열을 date 객체로 정규화한다.

    '2026-03-19', '2026.03.19', '2026/03/19', '26.03.19' 등 지원.
    미래 날짜이면 None 반환.
    """
    if raw is None:
        return None

    text = str(raw).strip()
    if not text or text.lower() == "null":
        return None

    # 구분자 통일
    text = text.replace(".", "-").replace("/", "-")

    # 패턴 매칭
    match = re.match(r"^(\d{2,4})-(\d{1,2})-(\d{1,2})$", text)
    if not match:
        return None

    year_str, month_str, day_str = match.groups()
    year = int(year_str)
    month = int(month_str)
    day = int(day_str)

    # 2자리 연도 → 4자리 변환
    if year < 100:
        year += 2000

    try:
        result = date(year, month, day)
    except ValueError:
        return None

    # 미래 날짜 검증
    if result > date.today():
        return None

    return result


def _parse_ocr_response(response_text: str) -> OcrResult:
    """Claude Vision 응답 텍스트를 OcrResult로 파싱한다.

    1차: JSON 파싱 시도
    2차: 정규식 fallback
    3차: 최종 실패 → success=False, raw_text에 원본 저장
    """
    # JSON 블록 추출 (```json ... ``` 래핑 처리)
    cleaned = response_text.strip()
    if "```json" in cleaned:
        json_match = re.search(r"```json\s*(.*?)\s*```", cleaned, re.DOTALL)
        if json_match:
            cleaned = json_match.group(1).strip()
    elif "```" in cleaned:
        json_match = re.search(r"```\s*(.*?)\s*```", cleaned, re.DOTALL)
        if json_match:
            cleaned = json_match.group(1).strip()

    # ── 1차: JSON 파싱 ──
    try:
        data = json.loads(cleaned)

        # 에러 응답 처리 ("영수증이 아닙니다" 등)
        if "error" in data:
            return OcrResult(
                raw_text=data.get("error", response_text),
                success=False,
            )

        receipt_date = _normalize_date(data.get("date"))
        amount = _normalize_amount(data.get("amount"))
        store_name = data.get("store_name")
        if store_name and str(store_name).lower() == "null":
            store_name = None
        items = data.get("items")
        if isinstance(items, list):
            # 각 항목의 price를 int로 정규화
            normalized_items: list[dict[str, object]] = []
            for item in items:
                if isinstance(item, dict):
                    normalized_item: dict[str, object] = {"name": item.get("name", "")}
                    price = _normalize_amount(item.get("price"))
                    normalized_item["price"] = price
                    normalized_items.append(normalized_item)
            items = normalized_items if normalized_items else None
        else:
            items = None

        raw_text = data.get("raw_text", response_text)

        return OcrResult(
            receipt_date=receipt_date,
            amount=amount,
            store_name=str(store_name) if store_name else None,
            items=items,
            raw_text=str(raw_text),
            success=True,
        )

    except (json.JSONDecodeError, KeyError, TypeError):
        logger.warning("JSON 파싱 실패, 정규식 fallback 시도")

    # ── 2차: 정규식 fallback ──
    return _regex_fallback(response_text)


def _regex_fallback(text: str) -> OcrResult:
    """정규식으로 날짜·금액을 추출한다 (JSON 파싱 실패 시 fallback)."""
    # 날짜 추출
    date_match = re.search(r"(\d{2,4})[.\-/](\d{1,2})[.\-/](\d{1,2})", text)
    receipt_date: date | None = None
    if date_match:
        year_str, month_str, day_str = date_match.groups()
        year = int(year_str)
        if year < 100:
            year += 2000
        try:
            candidate = date(year, int(month_str), int(day_str))
            if candidate <= date.today():
                receipt_date = candidate
        except ValueError:
            pass

    # 금액 추출 — 합계/총/결제 키워드 근처 숫자
    amount: int | None = None
    amount_patterns = [
        r"합\s*계.*?(\d[\d,]+)\s*원?",
        r"총.*?(\d[\d,]+)\s*원?",
        r"결제.*?(\d[\d,]+)\s*원?",
    ]
    for pattern in amount_patterns:
        m = re.search(pattern, text)
        if m:
            amount = _normalize_amount(m.group(1))
            if amount is not None:
                break

    success = receipt_date is not None or amount is not None
    return OcrResult(
        receipt_date=receipt_date,
        amount=amount,
        raw_text=text,
        success=success,
    )


# ── 메인 진입점 ───────────────────────────────────────


async def extract_receipt_data(image_path: str) -> OcrResult:
    """영수증 이미지에서 OCR 데이터를 추출한다.

    3중 에러 방어:
      1차 — 이미지 로드 실패
      2차 — Claude API 호출 실패
      3차 — 응답 파싱 실패 (내부에서 정규식 fallback)

    Args:
        image_path: 영수증 이미지 파일 경로.

    Returns:
        OcrResult — success=False이면 raw_text에 에러 메시지 포함.
    """
    # ── 1차: 이미지 로드 ──
    try:
        b64_data, media_type = _load_and_encode_image(image_path)
    except FileNotFoundError as e:
        logger.error("이미지 파일 없음: %s", e)
        return OcrResult(raw_text=f"이미지 로드 실패: {e}", success=False)
    except (ValueError, IOError) as e:
        logger.error("이미지 로드 에러: %s", e)
        return OcrResult(raw_text=f"이미지 로드 실패: {e}", success=False)

    # ── 2차: Claude API 호출 ──
    try:
        client = _get_client()
        response = await client.messages.create(
            model=VISION_MODEL,
            max_tokens=MAX_TOKENS,
            system=OCR_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": [
                        ImageBlockParam(
                            type="image",
                            source={
                                "type": "base64",
                                "media_type": media_type,  # type: ignore[typeddict-item]
                                "data": b64_data,
                            },
                        ),
                        TextBlockParam(
                            type="text",
                            text=OCR_USER_PROMPT,
                        ),
                    ],
                }
            ],
        )

        # 응답 텍스트 추출
        response_text = ""
        for block in response.content:
            if block.type == "text":
                response_text += block.text

        if not response_text.strip():
            return OcrResult(
                raw_text="Claude Vision 응답이 비어있습니다.",
                success=False,
            )

    except anthropic.AuthenticationError as e:
        logger.error("Claude API 인증 실패: %s", e)
        return OcrResult(
            raw_text=f"API 인증 실패: {e}",
            success=False,
        )
    except anthropic.RateLimitError as e:
        logger.error("Claude API 요청 한도 초과: %s", e)
        return OcrResult(
            raw_text=f"API 요청 한도 초과: {e}",
            success=False,
        )
    except anthropic.APIConnectionError as e:
        logger.error("Claude API 연결 실패: %s", e)
        return OcrResult(
            raw_text=f"API 연결 실패: {e}",
            success=False,
        )
    except anthropic.APIError as e:
        logger.error("Claude API 에러: %s", e)
        return OcrResult(
            raw_text=f"API 에러: {e}",
            success=False,
        )
    except Exception as e:
        logger.error("예상치 못한 에러: %s", e)
        return OcrResult(
            raw_text=f"OCR 처리 실패: {e}",
            success=False,
        )

    # ── 3차: 응답 파싱 (내부에서 JSON → 정규식 fallback) ──
    return _parse_ocr_response(response_text)
