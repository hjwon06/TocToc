"""통합 테스트 conftest — 실제 DB + OCR mock.

QA 에이전트 소유.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from app.services.ocr import OcrResult


def _make_ocr_result(
    receipt_date: date | None = None,
    amount: int | None = None,
    raw_text: str = "mock ocr",
    success: bool = True,
) -> OcrResult:
    """테스트용 OcrResult 생성."""
    return OcrResult(
        receipt_date=receipt_date or date(2026, 3, 15),
        amount=amount or 12000,
        raw_text=raw_text,
        success=success,
    )


@pytest.fixture(autouse=True)
def mock_ocr():
    """OCR API 호출을 mock하여 비용 방지."""
    with patch(
        "app.routers.receipts.extract_receipt_data",
        new_callable=AsyncMock,
        return_value=_make_ocr_result(),
    ) as m:
        yield m


@pytest.fixture(autouse=True)
def mock_thumbnail():
    """썸네일 생성 mock."""
    with patch("app.routers.receipts.create_thumbnail"):
        yield


@pytest.fixture(autouse=True)
def mock_save_upload():
    """파일 저장 mock."""
    call_count = 0

    async def _fake_save(file, upload_dir):
        nonlocal call_count
        call_count += 1
        filename = file.filename or "unknown"
        ext = filename.rsplit(".", maxsplit=1)[-1].lower() if "." in filename else "jpg"
        if ext not in {"jpg", "jpeg", "png", "heic"}:
            raise ValueError(f"허용되지 않는 확장자: .{ext}")
        return f"{upload_dir}/fake_{call_count}.{ext}"

    with patch(
        "app.routers.receipts.save_upload",
        side_effect=_fake_save,
    ) as m:
        yield m


@pytest.fixture(autouse=True)
def mock_validate_file_size():
    """파일 크기 검증 mock — 항상 통과."""
    with patch(
        "app.routers.receipts.validate_file_size",
        new_callable=AsyncMock,
        return_value=True,
    ) as m:
        yield m


@pytest.fixture
def mock_ocr_failure(mock_ocr):
    """OCR 실패 mock."""
    mock_ocr.return_value = _make_ocr_result(
        receipt_date=None,
        amount=None,
        raw_text="OCR 처리 실패",
        success=False,
    )
    return mock_ocr
