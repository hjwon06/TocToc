"""고도화 테스트 — 이미지 압축 + 재OCR + 인보이스 페이지.

QA 에이전트 소유.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from PIL import Image

from app.services.image import COMPRESS_MAX_LONG_SIDE, compress_image


# ── 이미지 압축 테스트 ──


class TestCompressImage:
    def test_large_image_resized(self, tmp_path: Path) -> None:
        """큰 이미지 → 긴 변 1920px로 리사이즈."""
        img = Image.new("RGB", (4000, 3000), "red")
        path = tmp_path / "big.jpg"
        img.save(path, "JPEG")

        result = compress_image(str(path))
        assert result is True

        with Image.open(path) as compressed:
            assert max(compressed.size) == COMPRESS_MAX_LONG_SIDE

    def test_small_image_not_resized(self, tmp_path: Path) -> None:
        """작은 이미지 → 크기 유지, 재압축만."""
        img = Image.new("RGB", (800, 600), "blue")
        path = tmp_path / "small.jpg"
        img.save(path, "JPEG")

        result = compress_image(str(path))
        assert result is True

        with Image.open(path) as compressed:
            assert compressed.size == (800, 600)

    def test_file_not_found(self) -> None:
        """존재하지 않는 파일 → False."""
        assert compress_image("nonexistent.jpg") is False


# ── 재OCR 테스트 ──


@pytest.mark.asyncio
async def test_retry_ocr_success(client: AsyncClient) -> None:
    """재OCR 성공 → 날짜/금액 업데이트."""
    from app.services.ocr import OcrResult
    from datetime import date

    # 먼저 업로드 (OCR 실패 상태)
    with patch("app.routers.receipts.extract_receipt_data", new_callable=AsyncMock) as mock_ocr, \
         patch("app.routers.receipts.save_upload", new_callable=AsyncMock, return_value="static/uploads/test.jpg"), \
         patch("app.routers.receipts.validate_file_size", new_callable=AsyncMock, return_value=True), \
         patch("app.routers.receipts.create_thumbnail"), \
         patch("app.routers.receipts.compress_image"):

        mock_ocr.return_value = OcrResult(success=False, raw_text="OCR 실패")
        files = [("files", ("r.jpg", BytesIO(b"img"), "image/jpeg"))]
        resp = await client.post("/api/receipts/upload", files=files)
        assert resp.status_code == 201
        rid = resp.json()["uploaded"][0]["id"]

    # 재OCR — 성공으로 mock
    with patch("app.routers.receipts.extract_receipt_data", new_callable=AsyncMock) as mock_ocr2:
        mock_ocr2.return_value = OcrResult(
            receipt_date=date(2026, 3, 20), amount=8500,
            raw_text="CU 편의점", success=True,
        )
        resp = await client.post(f"/api/receipts/{rid}/retry-ocr")
        assert resp.status_code == 200
        data = resp.json()
        assert data["receipt_date"] == "2026-03-20"
        assert data["amount"] == 8500
        assert data["is_manual"] is False


@pytest.mark.asyncio
async def test_retry_ocr_still_fails(client: AsyncClient) -> None:
    """재OCR 실패 → is_manual 유지."""
    from app.services.ocr import OcrResult

    with patch("app.routers.receipts.extract_receipt_data", new_callable=AsyncMock) as mock_ocr, \
         patch("app.routers.receipts.save_upload", new_callable=AsyncMock, return_value="static/uploads/test2.jpg"), \
         patch("app.routers.receipts.validate_file_size", new_callable=AsyncMock, return_value=True), \
         patch("app.routers.receipts.create_thumbnail"), \
         patch("app.routers.receipts.compress_image"):

        mock_ocr.return_value = OcrResult(success=False, raw_text="실패")
        files = [("files", ("r.jpg", BytesIO(b"img"), "image/jpeg"))]
        resp = await client.post("/api/receipts/upload", files=files)
        rid = resp.json()["uploaded"][0]["id"]

    with patch("app.routers.receipts.extract_receipt_data", new_callable=AsyncMock) as mock_ocr2:
        mock_ocr2.return_value = OcrResult(success=False, raw_text="재시도도 실패")
        resp = await client.post(f"/api/receipts/{rid}/retry-ocr")
        assert resp.status_code == 200
        assert resp.json()["is_manual"] is True


@pytest.mark.asyncio
async def test_retry_ocr_not_found(client: AsyncClient) -> None:
    """존재하지 않는 영수증 재OCR → 404."""
    resp = await client.post("/api/receipts/99999/retry-ocr")
    assert resp.status_code == 404


# ── 인보이스 페이지 테스트 ──


@pytest.mark.asyncio
async def test_invoice_page_loads(client: AsyncClient) -> None:
    """/invoice 페이지 정상 로드."""
    resp = await client.get("/invoice")
    assert resp.status_code == 200
    assert "식비 인보이스" in resp.text


@pytest.mark.asyncio
async def test_invoice_preview(client: AsyncClient) -> None:
    """인보이스 미리보기 API."""
    resp = await client.get("/api/receipts/invoice-preview", params={"month": "2026-03"})
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


@pytest.mark.asyncio
async def test_invoice_preview_with_data(client: AsyncClient) -> None:
    """데이터 있는 월 미리보기 → 금액 캡 적용."""
    from app.services.ocr import OcrResult
    from datetime import date

    with patch("app.routers.receipts.extract_receipt_data", new_callable=AsyncMock) as mock_ocr, \
         patch("app.routers.receipts.save_upload", new_callable=AsyncMock, return_value="static/uploads/inv.jpg"), \
         patch("app.routers.receipts.validate_file_size", new_callable=AsyncMock, return_value=True), \
         patch("app.routers.receipts.create_thumbnail"), \
         patch("app.routers.receipts.compress_image"):

        mock_ocr.return_value = OcrResult(
            receipt_date=date(2026, 3, 10), amount=15000,
            raw_text="test", success=True,
        )
        files = [("files", ("r.jpg", BytesIO(b"img"), "image/jpeg"))]
        await client.post("/api/receipts/upload", files=files)

    resp = await client.get("/api/receipts/invoice-preview", params={"month": "2026-03"})
    assert resp.status_code == 200
    # 15000 → 캡 10000
    assert "10,000" in resp.text
