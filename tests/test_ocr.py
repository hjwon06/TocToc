"""OCR 서비스 테스트 — A3 AI/OCR 에이전트."""

from __future__ import annotations

import base64
import json
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.services.ocr import (
    _extract_from_text,
    _load_and_encode_image,
    _normalize_amount,
    _normalize_date,
    _parse_clova_response,
    extract_receipt_data,
)

# ── _normalize_amount 단위 테스트 ──────────────────────


class TestNormalizeAmount:
    """금액 정규화 테스트."""

    def test_integer_string(self) -> None:
        assert _normalize_amount("12500") == 12500

    def test_comma_separated(self) -> None:
        assert _normalize_amount("12,500") == 12500

    def test_with_won(self) -> None:
        assert _normalize_amount("12,500원") == 12500

    def test_with_spaces(self) -> None:
        assert _normalize_amount(" 8,500 원 ") == 8500

    def test_integer_value(self) -> None:
        assert _normalize_amount(15000) == 15000

    def test_float_rounds(self) -> None:
        assert _normalize_amount("12500.7") == 12501

    def test_zero_returns_none(self) -> None:
        assert _normalize_amount("0") is None

    def test_negative_returns_none(self) -> None:
        assert _normalize_amount("-5000") is None

    def test_none_returns_none(self) -> None:
        assert _normalize_amount(None) is None

    def test_null_string_returns_none(self) -> None:
        assert _normalize_amount("null") is None

    def test_empty_string_returns_none(self) -> None:
        assert _normalize_amount("") is None

    def test_non_numeric_returns_none(self) -> None:
        assert _normalize_amount("abc") is None


# ── _normalize_date 단위 테스트 ────────────────────────


class TestNormalizeDate:
    """날짜 정규화 테스트."""

    def test_iso_format(self) -> None:
        assert _normalize_date("2025-03-19") == date(2025, 3, 19)

    def test_dot_format(self) -> None:
        assert _normalize_date("2025.03.19") == date(2025, 3, 19)

    def test_slash_format(self) -> None:
        assert _normalize_date("2025/03/19") == date(2025, 3, 19)

    def test_two_digit_year(self) -> None:
        assert _normalize_date("25.03.19") == date(2025, 3, 19)

    def test_future_date_returns_none(self) -> None:
        """미래 날짜이면 None."""
        assert _normalize_date("2099-12-31") is None

    def test_none_returns_none(self) -> None:
        assert _normalize_date(None) is None

    def test_null_string_returns_none(self) -> None:
        assert _normalize_date("null") is None

    def test_empty_string_returns_none(self) -> None:
        assert _normalize_date("") is None

    def test_invalid_date_returns_none(self) -> None:
        """유효하지 않은 날짜."""
        assert _normalize_date("2025-13-40") is None

    def test_no_separator_returns_none(self) -> None:
        """구분자 없는 문자열."""
        assert _normalize_date("20250319") is None


# ── _parse_clova_response 단위 테스트 ─────────────────


class TestParseClovaResponse:
    """CLOVA OCR 응답 파싱 테스트."""

    def test_success_response(self) -> None:
        """정상 SUCCESS 응답 — 날짜/금액 추출."""
        resp = {
            "version": "V2",
            "requestId": "test-uuid",
            "timestamp": 123456,
            "images": [
                {
                    "uid": "img1",
                    "name": "receipt",
                    "inferResult": "SUCCESS",
                    "fields": [
                        {"inferText": "맛있는 식당", "inferConfidence": 0.99},
                        {"inferText": "2025-03-15", "inferConfidence": 0.95},
                        {"inferText": "합계", "inferConfidence": 0.98},
                        {"inferText": "12,500원", "inferConfidence": 0.97},
                    ],
                }
            ],
        }
        result = _parse_clova_response(resp)

        assert result.success is True
        assert result.receipt_date == date(2025, 3, 15)
        assert result.amount == 12500

    def test_failure_response(self) -> None:
        """inferResult가 FAILURE인 경우."""
        resp = {
            "images": [
                {
                    "inferResult": "FAILURE",
                    "message": "이미지 인식 실패",
                    "fields": [],
                }
            ],
        }
        result = _parse_clova_response(resp)

        assert result.success is False
        assert "CLOVA OCR 실패" in result.raw_text

    def test_empty_images(self) -> None:
        """images 배열이 비어 있는 경우."""
        resp: dict[str, object] = {"images": []}
        result = _parse_clova_response(resp)

        assert result.success is False
        assert "이미지 결과 없음" in result.raw_text

    def test_no_images_key(self) -> None:
        """images 키 자체가 없는 경우."""
        resp = {"version": "V2"}
        result = _parse_clova_response(resp)

        assert result.success is False
        assert "이미지 결과 없음" in result.raw_text

    def test_empty_fields(self) -> None:
        """fields가 비어 있어 텍스트 없음."""
        resp = {
            "images": [
                {
                    "inferResult": "SUCCESS",
                    "fields": [],
                }
            ],
        }
        result = _parse_clova_response(resp)

        assert result.success is False
        assert "텍스트 없음" in result.raw_text

    def test_only_date_no_amount(self) -> None:
        """날짜만 있고 금액 없는 경우 (1000원 미만 숫자만 존재)."""
        resp = {
            "images": [
                {
                    "inferResult": "SUCCESS",
                    "fields": [
                        {"inferText": "25.03.15", "inferConfidence": 0.95},
                        {"inferText": "수량 2", "inferConfidence": 0.90},
                    ],
                }
            ],
        }
        result = _parse_clova_response(resp)

        assert result.success is True
        assert result.receipt_date == date(2025, 3, 15)
        assert result.amount is None

    def test_only_amount_no_date(self) -> None:
        """금액만 있고 날짜 없는 경우."""
        resp = {
            "images": [
                {
                    "inferResult": "SUCCESS",
                    "fields": [
                        {"inferText": "합계", "inferConfidence": 0.98},
                        {"inferText": "8,500원", "inferConfidence": 0.97},
                    ],
                }
            ],
        }
        result = _parse_clova_response(resp)

        assert result.success is True
        assert result.receipt_date is None
        assert result.amount == 8500

    def test_dot_date_format(self) -> None:
        """점(.) 구분 날짜."""
        resp = {
            "images": [
                {
                    "inferResult": "SUCCESS",
                    "fields": [
                        {"inferText": "2025.03.15", "inferConfidence": 0.95},
                        {"inferText": "합계 5,000원", "inferConfidence": 0.97},
                    ],
                }
            ],
        }
        result = _parse_clova_response(resp)

        assert result.receipt_date == date(2025, 3, 15)

    def test_future_date_becomes_none(self) -> None:
        """미래 날짜이면 None으로 처리."""
        resp = {
            "images": [
                {
                    "inferResult": "SUCCESS",
                    "fields": [
                        {"inferText": "2099-12-31", "inferConfidence": 0.95},
                        {"inferText": "합계 5,000원", "inferConfidence": 0.97},
                    ],
                }
            ],
        }
        result = _parse_clova_response(resp)

        assert result.success is True
        assert result.receipt_date is None
        assert result.amount == 5000

    def test_comma_amount(self) -> None:
        """콤마가 포함된 금액."""
        resp = {
            "images": [
                {
                    "inferResult": "SUCCESS",
                    "fields": [
                        {"inferText": "2025-03-15", "inferConfidence": 0.95},
                        {"inferText": "합계 12,500", "inferConfidence": 0.97},
                    ],
                }
            ],
        }
        result = _parse_clova_response(resp)

        assert result.amount == 12500

    def test_failure_with_message(self) -> None:
        """FAILURE 응답에 커스텀 메시지 포함."""
        resp = {
            "images": [
                {
                    "inferResult": "FAILURE",
                    "message": "Image too small",
                    "fields": [],
                }
            ],
        }
        result = _parse_clova_response(resp)

        assert result.success is False
        assert "Image too small" in result.raw_text


# ── _extract_from_text 단위 테스트 ─────────────────────


class TestExtractFromText:
    """텍스트에서 날짜/금액 정규식 추출 테스트."""

    def test_extract_date_and_amount(self) -> None:
        text = "영수증 2025-03-15 합계 12,500원"
        result = _extract_from_text(text)
        assert result.receipt_date == date(2025, 3, 15)
        assert result.amount == 12500
        assert result.success is True

    def test_total_keyword(self) -> None:
        text = "총 결제금액 8,000원"
        result = _extract_from_text(text)
        assert result.amount == 8000

    def test_payment_keyword(self) -> None:
        text = "결제 금액: 15,000"
        result = _extract_from_text(text)
        assert result.amount == 15000

    def test_card_keyword(self) -> None:
        """카드 키워드 근처 금액 추출."""
        text = "카드 결제 9,500원"
        result = _extract_from_text(text)
        assert result.amount == 9500

    def test_approval_keyword(self) -> None:
        """승인 키워드 근처 금액 추출."""
        text = "승인금액 7,200원"
        result = _extract_from_text(text)
        assert result.amount == 7200

    def test_largest_amount_fallback(self) -> None:
        """키워드 없을 때 가장 큰 금액 fallback."""
        text = "김치찌개 8,000 공기밥 1,000 음료 2,500"
        result = _extract_from_text(text)
        assert result.amount == 8000

    def test_no_data_found(self) -> None:
        text = "이 텍스트에는 날짜도 금액도 없습니다."
        result = _extract_from_text(text)
        assert result.success is False
        assert result.receipt_date is None
        assert result.amount is None

    def test_dot_separated_date(self) -> None:
        text = "날짜: 2025.01.20 합계: 6,000원"
        result = _extract_from_text(text)
        assert result.receipt_date == date(2025, 1, 20)

    def test_small_amounts_ignored_in_fallback(self) -> None:
        """1000원 미만 금액은 fallback에서 무시."""
        text = "수량 3 단가 500"
        result = _extract_from_text(text)
        assert result.amount is None


# ── _load_and_encode_image 단위 테스트 ────────────────


class TestLoadAndEncodeImage:
    """이미지 로드/인코딩 테스트."""

    def test_jpeg_success(self, tmp_path: Path) -> None:
        """JPEG 파일 정상 로드."""
        img_file = tmp_path / "test.jpg"
        img_file.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg-data")

        b64_data, fmt = _load_and_encode_image(str(img_file))

        assert fmt == "jpg"
        decoded = base64.standard_b64decode(b64_data)
        assert decoded == b"\xff\xd8\xff\xe0fake-jpeg-data"

    def test_png_success(self, tmp_path: Path) -> None:
        """PNG 파일 정상 로드."""
        img_file = tmp_path / "test.png"
        img_file.write_bytes(b"\x89PNGfake-png-data")

        b64_data, fmt = _load_and_encode_image(str(img_file))

        assert fmt == "png"
        decoded = base64.standard_b64decode(b64_data)
        assert decoded == b"\x89PNGfake-png-data"

    def test_bmp_success(self, tmp_path: Path) -> None:
        """BMP 파일 정상 로드 (CLOVA OCR 지원)."""
        img_file = tmp_path / "test.bmp"
        img_file.write_bytes(b"bmp-data")

        b64_data, fmt = _load_and_encode_image(str(img_file))

        assert fmt == "bmp"
        decoded = base64.standard_b64decode(b64_data)
        assert decoded == b"bmp-data"

    def test_file_not_found(self) -> None:
        """존재하지 않는 파일."""
        with pytest.raises(FileNotFoundError, match="이미지 파일을 찾을 수 없습니다"):
            _load_and_encode_image("/nonexistent/path/image.jpg")

    def test_unsupported_extension(self, tmp_path: Path) -> None:
        """지원하지 않는 확장자."""
        img_file = tmp_path / "test.svg"
        img_file.write_bytes(b"svg-data")

        with pytest.raises(ValueError, match="지원하지 않는 이미지 형식"):
            _load_and_encode_image(str(img_file))

    def test_heic_without_pillow_heif(self, tmp_path: Path) -> None:
        """HEIC 파일 — pillow-heif 미설치 시 graceful fallback."""
        img_file = tmp_path / "test.heic"
        img_file.write_bytes(b"fake-heic-data")

        # pillow_heif import 실패 시뮬레이션
        with patch.dict("sys.modules", {"pillow_heif": None}):
            b64_data, fmt = _load_and_encode_image(str(img_file))

        assert fmt == "jpg"
        decoded = base64.standard_b64decode(b64_data)
        assert decoded == b"fake-heic-data"


# ── extract_receipt_data 통합 테스트 ──────────────────


def _make_clova_response(
    fields: list[dict[str, object]] | None = None,
    infer_result: str = "SUCCESS",
    status_code: int = 200,
    message: str = "",
) -> httpx.Response:
    """CLOVA OCR API httpx 응답 mock 생성."""
    if status_code != 200:
        return httpx.Response(status_code=status_code, content=message.encode())

    if fields is None:
        fields = []

    body = json.dumps({
        "version": "V2",
        "requestId": "test-uuid",
        "timestamp": 123456,
        "images": [
            {
                "uid": "img1",
                "name": "receipt",
                "inferResult": infer_result,
                "message": message,
                "fields": fields,
            }
        ],
    }).encode()
    return httpx.Response(status_code=200, content=body)


class TestExtractReceiptData:
    """extract_receipt_data 통합 테스트 (API mock)."""

    @pytest.mark.asyncio
    async def test_success(self, tmp_path: Path) -> None:
        """정상 OCR 흐름."""
        img_file = tmp_path / "receipt.jpg"
        img_file.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")

        mock_resp = _make_clova_response(
            fields=[
                {"inferText": "맛있는 식당", "inferConfidence": 0.99},
                {"inferText": "2025-03-15", "inferConfidence": 0.95},
                {"inferText": "합계", "inferConfidence": 0.98},
                {"inferText": "12,500원", "inferConfidence": 0.97},
            ],
        )

        with patch("app.services.ocr.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.post = AsyncMock(return_value=mock_resp)
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            result = await extract_receipt_data(str(img_file))

        assert result.success is True
        assert result.receipt_date == date(2025, 3, 15)
        assert result.amount == 12500

    @pytest.mark.asyncio
    async def test_api_error(self, tmp_path: Path) -> None:
        """API 500 에러 발생 시."""
        img_file = tmp_path / "receipt.jpg"
        img_file.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")

        mock_resp = _make_clova_response(
            status_code=500, message="Internal Server Error"
        )

        with patch("app.services.ocr.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.post = AsyncMock(return_value=mock_resp)
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            result = await extract_receipt_data(str(img_file))

        assert result.success is False
        assert "API 에러" in result.raw_text

    @pytest.mark.asyncio
    async def test_rate_limit_error(self, tmp_path: Path) -> None:
        """Rate limit 에러 발생 시."""
        img_file = tmp_path / "receipt.jpg"
        img_file.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")

        mock_resp = _make_clova_response(
            status_code=429, message="Rate limit exceeded"
        )

        with patch("app.services.ocr.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.post = AsyncMock(return_value=mock_resp)
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            result = await extract_receipt_data(str(img_file))

        assert result.success is False
        assert "요청 한도 초과" in result.raw_text

    @pytest.mark.asyncio
    async def test_auth_error(self, tmp_path: Path) -> None:
        """인증 에러 발생 시."""
        img_file = tmp_path / "receipt.jpg"
        img_file.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")

        mock_resp = _make_clova_response(
            status_code=401, message="Invalid Secret Key"
        )

        with patch("app.services.ocr.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.post = AsyncMock(return_value=mock_resp)
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            result = await extract_receipt_data(str(img_file))

        assert result.success is False
        assert "인증 실패" in result.raw_text

    @pytest.mark.asyncio
    async def test_connection_error(self, tmp_path: Path) -> None:
        """네트워크 연결 에러 발생 시."""
        img_file = tmp_path / "receipt.jpg"
        img_file.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")

        with patch("app.services.ocr.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.post = AsyncMock(
                side_effect=httpx.ConnectError("Connection refused")
            )
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            result = await extract_receipt_data(str(img_file))

        assert result.success is False
        assert "연결 실패" in result.raw_text

    @pytest.mark.asyncio
    async def test_image_not_found(self) -> None:
        """이미지 파일이 없을 때."""
        result = await extract_receipt_data("/nonexistent/receipt.jpg")

        assert result.success is False
        assert "이미지 로드 실패" in result.raw_text

    @pytest.mark.asyncio
    async def test_empty_response(self, tmp_path: Path) -> None:
        """CLOVA OCR이 빈 fields를 반환할 때."""
        img_file = tmp_path / "receipt.jpg"
        img_file.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")

        mock_resp = _make_clova_response(fields=[])

        with patch("app.services.ocr.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.post = AsyncMock(return_value=mock_resp)
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            result = await extract_receipt_data(str(img_file))

        assert result.success is False
        assert "텍스트 없음" in result.raw_text

    @pytest.mark.asyncio
    async def test_not_receipt_image(self, tmp_path: Path) -> None:
        """영수증이 아닌 이미지 — inferResult=FAILURE."""
        img_file = tmp_path / "cat.jpg"
        img_file.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")

        mock_resp = _make_clova_response(
            infer_result="FAILURE", message="OCR 실패"
        )

        with patch("app.services.ocr.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.post = AsyncMock(return_value=mock_resp)
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            result = await extract_receipt_data(str(img_file))

        assert result.success is False
        assert "CLOVA OCR 실패" in result.raw_text

    @pytest.mark.asyncio
    async def test_text_extraction_from_fields(
        self, tmp_path: Path
    ) -> None:
        """CLOVA 응답 fields에서 텍스트 추출 후 정규식 매칭."""
        img_file = tmp_path / "receipt.png"
        img_file.write_bytes(b"\x89PNGfake-png")

        mock_resp = _make_clova_response(
            fields=[
                {"inferText": "맛있는 식당", "inferConfidence": 0.99},
                {"inferText": "2025.03.15", "inferConfidence": 0.95},
                {"inferText": "김치찌개 8000", "inferConfidence": 0.90},
                {"inferText": "합계 12,500원", "inferConfidence": 0.97},
            ],
        )

        with patch("app.services.ocr.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.post = AsyncMock(return_value=mock_resp)
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            result = await extract_receipt_data(str(img_file))

        assert result.receipt_date == date(2025, 3, 15)
        assert result.amount == 12500
        assert result.success is True

    @pytest.mark.asyncio
    async def test_no_api_config(self, tmp_path: Path) -> None:
        """CLOVA OCR 설정이 없을 때."""
        img_file = tmp_path / "receipt.jpg"
        img_file.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")

        with patch("app.services.ocr.settings") as mock_settings:
            mock_settings.CLOVA_OCR_SECRET = ""
            mock_settings.CLOVA_OCR_URL = ""

            result = await extract_receipt_data(str(img_file))

        assert result.success is False
        assert "CLOVA_OCR" in result.raw_text or "OCR 처리 실패" in result.raw_text
