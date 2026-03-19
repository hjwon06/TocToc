"""OCR 서비스 테스트 — A3 AI/OCR 에이전트."""

from __future__ import annotations

import base64
import json
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import anthropic
import pytest

from app.services.ocr import (
    _load_and_encode_image,
    _normalize_amount,
    _normalize_date,
    _parse_ocr_response,
    _regex_fallback,
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


# ── _parse_ocr_response 단위 테스트 ───────────────────


class TestParseOcrResponse:
    """OCR 응답 파싱 테스트."""

    def test_valid_json(self) -> None:
        """정상 JSON 응답."""
        response = json.dumps({
            "date": "2025-03-15",
            "amount": 12500,
            "items": [{"name": "김치찌개", "price": 8000}, {"name": "공기밥", "price": 1000}],
            "store_name": "맛있는 식당",
            "raw_text": "맛있는 식당\n김치찌개 8,000\n공기밥 1,000\n합계 12,500",
        })
        result = _parse_ocr_response(response)

        assert result.success is True
        assert result.receipt_date == date(2025, 3, 15)
        assert result.amount == 12500
        assert result.store_name == "맛있는 식당"
        assert result.items is not None
        assert len(result.items) == 2

    def test_comma_amount(self) -> None:
        """콤마가 포함된 금액."""
        response = json.dumps({
            "date": "2025-03-15",
            "amount": "12,500",
            "store_name": "식당",
            "raw_text": "test",
        })
        result = _parse_ocr_response(response)
        assert result.amount == 12500

    def test_won_amount(self) -> None:
        """'원'이 붙은 금액."""
        response = json.dumps({
            "date": "2025-03-15",
            "amount": "8,500원",
            "store_name": None,
            "raw_text": "test",
        })
        result = _parse_ocr_response(response)
        assert result.amount == 8500

    def test_null_amount(self) -> None:
        """금액이 null."""
        response = json.dumps({
            "date": "2025-03-15",
            "amount": None,
            "store_name": "식당",
            "raw_text": "test",
        })
        result = _parse_ocr_response(response)
        assert result.success is True
        assert result.amount is None

    def test_null_date(self) -> None:
        """날짜가 null."""
        response = json.dumps({
            "date": None,
            "amount": 5000,
            "store_name": "식당",
            "raw_text": "test",
        })
        result = _parse_ocr_response(response)
        assert result.success is True
        assert result.receipt_date is None
        assert result.amount == 5000

    def test_dot_date_format(self) -> None:
        """점(.) 구분 날짜."""
        response = json.dumps({
            "date": "2025.03.15",
            "amount": 5000,
            "raw_text": "test",
        })
        result = _parse_ocr_response(response)
        assert result.receipt_date == date(2025, 3, 15)

    def test_future_date_becomes_none(self) -> None:
        """미래 날짜이면 None으로 처리."""
        response = json.dumps({
            "date": "2099-12-31",
            "amount": 5000,
            "raw_text": "test",
        })
        result = _parse_ocr_response(response)
        assert result.success is True
        assert result.receipt_date is None

    def test_error_response(self) -> None:
        """'영수증이 아닙니다' 에러 응답."""
        response = json.dumps({"error": "영수증이 아닙니다"})
        result = _parse_ocr_response(response)
        assert result.success is False
        assert "영수증이 아닙니다" in result.raw_text

    def test_invalid_json_regex_fallback(self) -> None:
        """잘못된 JSON → 정규식 fallback."""
        response = "맛있는 식당 2025-03-15\n김치찌개 8000\n합계 12,500원"
        result = _parse_ocr_response(response)
        # 정규식 fallback으로 날짜/금액 추출 시도
        assert result.receipt_date == date(2025, 3, 15)
        assert result.amount == 12500

    def test_json_in_code_block(self) -> None:
        """```json ... ``` 래핑된 응답."""
        inner = json.dumps({
            "date": "2025-03-15",
            "amount": 9000,
            "raw_text": "test",
        })
        response = f"```json\n{inner}\n```"
        result = _parse_ocr_response(response)
        assert result.success is True
        assert result.amount == 9000

    def test_store_name_null_string(self) -> None:
        """store_name이 문자열 'null'이면 None으로 처리."""
        response = json.dumps({
            "date": "2025-03-15",
            "amount": 5000,
            "store_name": "null",
            "raw_text": "test",
        })
        result = _parse_ocr_response(response)
        assert result.store_name is None

    def test_items_normalization(self) -> None:
        """items 내부 price 정규화."""
        response = json.dumps({
            "date": "2025-03-15",
            "amount": 15000,
            "items": [
                {"name": "비빔밥", "price": "8,000원"},
                {"name": "음료", "price": 3000},
            ],
            "raw_text": "test",
        })
        result = _parse_ocr_response(response)
        assert result.items is not None
        assert result.items[0]["price"] == 8000
        assert result.items[1]["price"] == 3000


# ── _regex_fallback 단위 테스트 ───────────────────────


class TestRegexFallback:
    """정규식 fallback 테스트."""

    def test_extract_date_and_amount(self) -> None:
        text = "영수증 2025-03-15\n합계 12,500원"
        result = _regex_fallback(text)
        assert result.receipt_date == date(2025, 3, 15)
        assert result.amount == 12500
        assert result.success is True

    def test_total_keyword(self) -> None:
        text = "총 결제금액 8,000원"
        result = _regex_fallback(text)
        assert result.amount == 8000

    def test_payment_keyword(self) -> None:
        text = "결제 금액: 15,000"
        result = _regex_fallback(text)
        assert result.amount == 15000

    def test_no_data_found(self) -> None:
        text = "이 텍스트에는 날짜도 금액도 없습니다."
        result = _regex_fallback(text)
        assert result.success is False
        assert result.receipt_date is None
        assert result.amount is None

    def test_dot_separated_date(self) -> None:
        text = "날짜: 2025.01.20\n합계: 6,000원"
        result = _regex_fallback(text)
        assert result.receipt_date == date(2025, 1, 20)


# ── _load_and_encode_image 단위 테스트 ────────────────


class TestLoadAndEncodeImage:
    """이미지 로드/인코딩 테스트."""

    def test_jpeg_success(self, tmp_path: Path) -> None:
        """JPEG 파일 정상 로드."""
        img_file = tmp_path / "test.jpg"
        img_file.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg-data")

        b64_data, media_type = _load_and_encode_image(str(img_file))

        assert media_type == "image/jpeg"
        decoded = base64.standard_b64decode(b64_data)
        assert decoded == b"\xff\xd8\xff\xe0fake-jpeg-data"

    def test_png_success(self, tmp_path: Path) -> None:
        """PNG 파일 정상 로드."""
        img_file = tmp_path / "test.png"
        img_file.write_bytes(b"\x89PNGfake-png-data")

        b64_data, media_type = _load_and_encode_image(str(img_file))

        assert media_type == "image/png"
        decoded = base64.standard_b64decode(b64_data)
        assert decoded == b"\x89PNGfake-png-data"

    def test_file_not_found(self) -> None:
        """존재하지 않는 파일."""
        with pytest.raises(FileNotFoundError, match="이미지 파일을 찾을 수 없습니다"):
            _load_and_encode_image("/nonexistent/path/image.jpg")

    def test_unsupported_extension(self, tmp_path: Path) -> None:
        """지원하지 않는 확장자."""
        img_file = tmp_path / "test.bmp"
        img_file.write_bytes(b"bmp-data")

        with pytest.raises(ValueError, match="지원하지 않는 이미지 형식"):
            _load_and_encode_image(str(img_file))

    def test_heic_without_pillow_heif(self, tmp_path: Path) -> None:
        """HEIC 파일 — pillow-heif 미설치 시 graceful fallback."""
        img_file = tmp_path / "test.heic"
        img_file.write_bytes(b"fake-heic-data")

        # pillow_heif import 실패 시뮬레이션
        with patch.dict("sys.modules", {"pillow_heif": None}):
            b64_data, media_type = _load_and_encode_image(str(img_file))

        assert media_type == "image/jpeg"
        decoded = base64.standard_b64decode(b64_data)
        assert decoded == b"fake-heic-data"


# ── extract_receipt_data 통합 테스트 ──────────────────


def _make_mock_response(text: str) -> MagicMock:
    """Claude API 응답 mock 생성."""
    block = MagicMock()
    block.type = "text"
    block.text = text
    response = MagicMock()
    response.content = [block]
    return response


class TestExtractReceiptData:
    """extract_receipt_data 통합 테스트 (API mock)."""

    @pytest.mark.asyncio
    async def test_success(self, tmp_path: Path) -> None:
        """정상 OCR 흐름."""
        img_file = tmp_path / "receipt.jpg"
        img_file.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")

        api_response_text = json.dumps({
            "date": "2025-03-15",
            "amount": 12500,
            "items": [{"name": "김치찌개", "price": 8000}],
            "store_name": "맛있는 식당",
            "raw_text": "맛있는 식당 김치찌개 8,000 합계 12,500",
        })
        mock_response = _make_mock_response(api_response_text)

        with patch("app.services.ocr._get_client") as mock_client_fn:
            mock_client = AsyncMock()
            mock_client.messages.create = AsyncMock(return_value=mock_response)
            mock_client_fn.return_value = mock_client

            result = await extract_receipt_data(str(img_file))

        assert result.success is True
        assert result.receipt_date == date(2025, 3, 15)
        assert result.amount == 12500
        assert result.store_name == "맛있는 식당"

    @pytest.mark.asyncio
    async def test_api_error(self, tmp_path: Path) -> None:
        """Claude API 에러 발생 시."""
        img_file = tmp_path / "receipt.jpg"
        img_file.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")

        with patch("app.services.ocr._get_client") as mock_client_fn:
            mock_client = AsyncMock()
            mock_client.messages.create = AsyncMock(
                side_effect=anthropic.APIError(
                    message="Internal Server Error",
                    request=MagicMock(),
                    body=None,
                )
            )
            mock_client_fn.return_value = mock_client

            result = await extract_receipt_data(str(img_file))

        assert result.success is False
        assert "API 에러" in result.raw_text

    @pytest.mark.asyncio
    async def test_rate_limit_error(self, tmp_path: Path) -> None:
        """Rate limit 에러 발생 시."""
        img_file = tmp_path / "receipt.jpg"
        img_file.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")

        with patch("app.services.ocr._get_client") as mock_client_fn:
            mock_client = AsyncMock()
            mock_response = MagicMock()
            mock_response.status_code = 429
            mock_response.headers = {}
            mock_client.messages.create = AsyncMock(
                side_effect=anthropic.RateLimitError(
                    message="Rate limit exceeded",
                    response=mock_response,
                    body=None,
                )
            )
            mock_client_fn.return_value = mock_client

            result = await extract_receipt_data(str(img_file))

        assert result.success is False
        assert "요청 한도 초과" in result.raw_text

    @pytest.mark.asyncio
    async def test_auth_error(self, tmp_path: Path) -> None:
        """인증 에러 발생 시."""
        img_file = tmp_path / "receipt.jpg"
        img_file.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")

        with patch("app.services.ocr._get_client") as mock_client_fn:
            mock_client = AsyncMock()
            mock_response = MagicMock()
            mock_response.status_code = 401
            mock_response.headers = {}
            mock_client.messages.create = AsyncMock(
                side_effect=anthropic.AuthenticationError(
                    message="Invalid API key",
                    response=mock_response,
                    body=None,
                )
            )
            mock_client_fn.return_value = mock_client

            result = await extract_receipt_data(str(img_file))

        assert result.success is False
        assert "인증 실패" in result.raw_text

    @pytest.mark.asyncio
    async def test_connection_error(self, tmp_path: Path) -> None:
        """네트워크 연결 에러 발생 시."""
        img_file = tmp_path / "receipt.jpg"
        img_file.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")

        with patch("app.services.ocr._get_client") as mock_client_fn:
            mock_client = AsyncMock()
            mock_client.messages.create = AsyncMock(
                side_effect=anthropic.APIConnectionError(
                    request=MagicMock(),
                )
            )
            mock_client_fn.return_value = mock_client

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
        """Claude가 빈 응답을 반환할 때."""
        img_file = tmp_path / "receipt.jpg"
        img_file.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")

        mock_response = _make_mock_response("")

        with patch("app.services.ocr._get_client") as mock_client_fn:
            mock_client = AsyncMock()
            mock_client.messages.create = AsyncMock(return_value=mock_response)
            mock_client_fn.return_value = mock_client

            result = await extract_receipt_data(str(img_file))

        assert result.success is False
        assert "비어있습니다" in result.raw_text

    @pytest.mark.asyncio
    async def test_not_receipt_image(self, tmp_path: Path) -> None:
        """영수증이 아닌 이미지일 때."""
        img_file = tmp_path / "cat.jpg"
        img_file.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")

        api_response_text = json.dumps({"error": "영수증이 아닙니다"})
        mock_response = _make_mock_response(api_response_text)

        with patch("app.services.ocr._get_client") as mock_client_fn:
            mock_client = AsyncMock()
            mock_client.messages.create = AsyncMock(return_value=mock_response)
            mock_client_fn.return_value = mock_client

            result = await extract_receipt_data(str(img_file))

        assert result.success is False
        assert "영수증이 아닙니다" in result.raw_text

    @pytest.mark.asyncio
    async def test_malformed_json_with_regex_fallback(
        self, tmp_path: Path
    ) -> None:
        """Claude가 잘못된 JSON을 반환 → 정규식 fallback."""
        img_file = tmp_path / "receipt.png"
        img_file.write_bytes(b"\x89PNGfake-png")

        # JSON이 아닌 일반 텍스트 응답
        raw = "맛있는 식당\n날짜: 2025.03.15\n김치찌개 8000\n합계 12,500원"
        mock_response = _make_mock_response(raw)

        with patch("app.services.ocr._get_client") as mock_client_fn:
            mock_client = AsyncMock()
            mock_client.messages.create = AsyncMock(return_value=mock_response)
            mock_client_fn.return_value = mock_client

            result = await extract_receipt_data(str(img_file))

        # 정규식으로 날짜/금액 추출됨
        assert result.receipt_date == date(2025, 3, 15)
        assert result.amount == 12500
        assert result.success is True

    @pytest.mark.asyncio
    async def test_no_api_key(self, tmp_path: Path) -> None:
        """API 키가 없을 때."""
        img_file = tmp_path / "receipt.jpg"
        img_file.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")

        with patch("app.services.ocr.settings") as mock_settings:
            mock_settings.ANTHROPIC_API_KEY = ""

            result = await extract_receipt_data(str(img_file))

        assert result.success is False
        assert "ANTHROPIC_API_KEY" in result.raw_text or "OCR 처리 실패" in result.raw_text
