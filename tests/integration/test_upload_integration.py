"""통합 테스트 — 업로드 플로우 (DB 저장 검증).

QA 에이전트 소유.
"""

from __future__ import annotations

from io import BytesIO
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_upload_saves_to_db(client: AsyncClient) -> None:
    """업로드 → DB 레코드 생성 확인."""
    files = [("files", ("receipt.jpg", BytesIO(b"fake"), "image/jpeg"))]
    resp = await client.post("/api/receipts/upload", files=files)
    assert resp.status_code == 201

    data = resp.json()
    assert data["total_uploaded"] == 1
    assert len(data["uploaded"]) == 1

    receipt = data["uploaded"][0]
    assert receipt["receipt_date"] == "2026-03-15"
    assert receipt["amount"] == 12000
    assert receipt["is_manual"] is False


@pytest.mark.asyncio
async def test_upload_multiple_partial_failure(
    client: AsyncClient,
    mock_save_upload: AsyncMock,
) -> None:
    """3장 중 1장 저장 실패 시 2장만 DB 저장."""
    call_count = 0

    async def _partial_fail(file, upload_dir):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise IOError("디스크 오류")
        return f"{upload_dir}/fake_{call_count}.jpg"

    mock_save_upload.side_effect = _partial_fail

    files = [
        ("files", ("a.jpg", BytesIO(b"a"), "image/jpeg")),
        ("files", ("b.jpg", BytesIO(b"b"), "image/jpeg")),
        ("files", ("c.jpg", BytesIO(b"c"), "image/jpeg")),
    ]
    resp = await client.post("/api/receipts/upload", files=files)
    assert resp.status_code == 201

    data = resp.json()
    assert data["total_uploaded"] == 2
    assert len(data["errors"]) == 1
    assert "디스크 오류" in data["errors"][0]


@pytest.mark.asyncio
async def test_upload_over_limit(client: AsyncClient) -> None:
    """21장 업로드 시 400 에러."""
    files = [
        ("files", (f"r{i}.jpg", BytesIO(b"x"), "image/jpeg"))
        for i in range(21)
    ]
    resp = await client.post("/api/receipts/upload", files=files)
    assert resp.status_code == 400
    assert "20" in resp.json()["error"]
