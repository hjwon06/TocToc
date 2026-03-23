"""Naver CLOVA OCR 서비스 — 영수증 이미지에서 날짜·금액 추출.

A3 AI/OCR 에이전트 소유 파일.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# ── 상수 ──────────────────────────────────────────────

SUPPORTED_EXTENSIONS: set[str] = {"jpg", "jpeg", "png", "gif", "webp", "tiff", "bmp", "pdf"}

# CLOVA OCR이 지원하는 format 값
EXT_TO_FORMAT: dict[str, str] = {
    "jpg": "jpg",
    "jpeg": "jpg",
    "png": "png",
    "gif": "gif",
    "webp": "webp",
    "tiff": "tiff",
    "bmp": "bmp",
    "pdf": "pdf",
}


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


def _get_clova_config() -> tuple[str, str]:
    """CLOVA OCR 설정을 반환한다."""
    secret = settings.CLOVA_OCR_SECRET
    url = settings.CLOVA_OCR_URL
    if not secret or not url:
        raise ValueError("CLOVA_OCR_SECRET 또는 CLOVA_OCR_URL이 설정되지 않았습니다.")
    return secret, url


def _load_and_encode_image(image_path: str) -> tuple[str, str]:
    """이미지 파일을 base64로 인코딩한다.

    Returns:
        (base64_data, format) 튜플. format은 CLOVA OCR용 (jpg, png 등).

    Raises:
        FileNotFoundError: 파일이 없을 때.
        ValueError: 지원하지 않는 확장자일 때.
        IOError: 파일 읽기/변환 실패 시.
    """
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"이미지 파일을 찾을 수 없습니다: {image_path}")

    ext = path.suffix.lstrip(".").lower()

    # HEIC 변환
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
            return b64, "jpg"
        except ImportError:
            logger.warning("pillow-heif 미설치 — HEIC를 JPEG로 가정하여 전송")
            raw_bytes = path.read_bytes()
            b64 = base64.standard_b64encode(raw_bytes).decode("utf-8")
            return b64, "jpg"
        except Exception as e:
            raise IOError(f"HEIC 변환 실패: {e}") from e

    fmt = EXT_TO_FORMAT.get(ext)
    if not fmt:
        raise ValueError(
            f"지원하지 않는 이미지 형식: .{ext} "
            f"(지원: {', '.join(sorted(SUPPORTED_EXTENSIONS))})"
        )

    raw_bytes = path.read_bytes()
    b64 = base64.standard_b64encode(raw_bytes).decode("utf-8")
    return b64, fmt


def _normalize_amount(raw: object) -> int | None:
    """금액 문자열을 원 단위 정수로 정규화한다."""
    if raw is None:
        return None

    text = str(raw).strip()
    if not text or text.lower() == "null":
        return None

    text = text.replace(",", "").replace("원", "").replace(" ", "")

    try:
        value = float(text)
    except (ValueError, TypeError):
        return None

    amount = round(value)
    if amount <= 0:
        return None
    return amount


def _normalize_date(raw: object) -> date | None:
    """날짜 문자열을 date 객체로 정규화한다."""
    if raw is None:
        return None

    text = str(raw).strip()
    if not text or text.lower() == "null":
        return None

    text = text.replace(".", "-").replace("/", "-")

    match = re.match(r"^(\d{2,4})-(\d{1,2})-(\d{1,2})$", text)
    if not match:
        return None

    year_str, month_str, day_str = match.groups()
    year = int(year_str)
    month = int(month_str)
    day = int(day_str)

    if year < 100:
        year += 2000

    try:
        result = date(year, month, day)
    except ValueError:
        return None

    if result > date.today():
        return None

    return result


def _parse_clova_response(resp_json: dict) -> OcrResult:
    """CLOVA OCR 응답을 파싱하여 OcrResult로 변환한다."""
    images = resp_json.get("images", [])
    if not images:
        return OcrResult(raw_text="CLOVA OCR 응답에 이미지 결과 없음", success=False)

    image_result = images[0]
    infer_result = image_result.get("inferResult", "")

    if infer_result != "SUCCESS":
        msg = image_result.get("message", "OCR 실패")
        return OcrResult(raw_text=f"CLOVA OCR 실패: {msg}", success=False)

    # 모든 필드의 텍스트를 추출
    fields = image_result.get("fields", [])
    texts = [f.get("inferText", "") for f in fields]
    raw_text = " ".join(texts)

    if not raw_text.strip():
        return OcrResult(raw_text="OCR 결과 텍스트 없음", success=False)

    # 정규식으로 날짜/금액 추출
    return _extract_from_text(raw_text)


def _extract_from_text(text: str) -> OcrResult:
    """OCR 텍스트에서 날짜와 금액을 정규식으로 추출한다."""
    # ── 날짜 추출 ──
    receipt_date: date | None = None
    date_patterns = [
        r"(\d{4})[.\-/\s](\d{1,2})[.\-/\s](\d{1,2})",  # 2026-03-15
        r"(\d{2})[.\-/\s](\d{1,2})[.\-/\s](\d{1,2})",   # 26-03-15
    ]
    for pattern in date_patterns:
        matches = re.findall(pattern, text)
        for match in matches:
            year = int(match[0])
            if year < 100:
                year += 2000
            try:
                candidate = date(year, int(match[1]), int(match[2]))
                if candidate <= date.today():
                    receipt_date = candidate
                    break
            except ValueError:
                continue
        if receipt_date:
            break

    # ── 금액 추출 ──
    amount: int | None = None

    # 1순위: 합계/총/결제 키워드 근처 금액
    amount_patterns = [
        r"합\s*계.*?(\d[\d,]+)\s*원?",
        r"총.*?(\d[\d,]+)\s*원?",
        r"결제.*?(\d[\d,]+)\s*원?",
        r"카드.*?(\d[\d,]+)\s*원?",
        r"승인.*?(\d[\d,]+)\s*원?",
    ]
    for pattern in amount_patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            amount = _normalize_amount(m.group(1))
            if amount is not None:
                break

    # 2순위: 가장 큰 금액 (1000원 이상)
    if amount is None:
        all_amounts = re.findall(r"(\d{1,3}(?:,\d{3})+|\d{4,})\s*원?", text)
        candidates = []
        for a in all_amounts:
            val = _normalize_amount(a)
            if val and val >= 1000:
                candidates.append(val)
        if candidates:
            amount = max(candidates)

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
      2차 — CLOVA OCR API 호출 실패
      3차 — 응답 파싱 실패

    Args:
        image_path: 영수증 이미지 파일 경로.

    Returns:
        OcrResult — success=False이면 raw_text에 에러 메시지 포함.
    """
    # ── 1차: 이미지 로드 ──
    try:
        b64_data, img_format = _load_and_encode_image(image_path)
    except FileNotFoundError as e:
        logger.error("이미지 파일 없음: %s", e)
        return OcrResult(raw_text=f"이미지 로드 실패: {e}", success=False)
    except (ValueError, IOError) as e:
        logger.error("이미지 로드 에러: %s", e)
        return OcrResult(raw_text=f"이미지 로드 실패: {e}", success=False)

    # ── 2차: CLOVA OCR API 호출 ──
    try:
        secret, ocr_url = _get_clova_config()

        payload = {
            "version": "V2",
            "requestId": str(uuid.uuid4()),
            "timestamp": int(time.time() * 1000),
            "lang": "ko",
            "images": [
                {
                    "format": img_format,
                    "name": "receipt",
                    "data": b64_data,
                }
            ],
        }

        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                ocr_url,
                headers={
                    "X-OCR-SECRET": secret,
                    "Content-Type": "application/json",
                },
                json=payload,
            )

        if response.status_code == 401:
            logger.error("CLOVA OCR 인증 실패")
            return OcrResult(raw_text="API 인증 실패: 잘못된 Secret Key", success=False)

        if response.status_code == 429:
            logger.error("CLOVA OCR 요청 한도 초과")
            return OcrResult(raw_text="API 요청 한도 초과", success=False)

        if response.status_code != 200:
            error_body = response.text
            logger.error("CLOVA OCR 에러 %d: %s", response.status_code, error_body)
            return OcrResult(
                raw_text=f"API 에러 ({response.status_code}): {error_body[:300]}",
                success=False,
            )

        resp_json = response.json()

    except httpx.ConnectError as e:
        logger.error("CLOVA OCR 연결 실패: %s", e)
        return OcrResult(raw_text=f"API 연결 실패: {e}", success=False)
    except httpx.TimeoutException as e:
        logger.error("CLOVA OCR 타임아웃: %s", e)
        return OcrResult(raw_text=f"API 타임아웃: {e}", success=False)
    except Exception as e:
        logger.error("예상치 못한 에러: %s", e)
        return OcrResult(raw_text=f"OCR 처리 실패: {e}", success=False)

    # ── 3차: 응답 파싱 ──
    return _parse_clova_response(resp_json)
