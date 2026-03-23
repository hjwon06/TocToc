"""통합 테스트 — 업로드 플로우 (DB 저장 검증).

QA 에이전트 소유.
"""

from __future__ import annotations

from datetime import date
from io import BytesIO
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Receipt


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
    assert receipt["amount_raw"] == 12000
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


# ── 같은 날짜 영수증 교체 통합 테스트 ─────────────────


@pytest.mark.asyncio
async def test_upload_replaces_existing_receipt(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """업로드 → 같은 날짜 재업로드 → DB에 1건만 존재."""
    # 1차 업로드
    files1 = [("files", ("first.jpg", BytesIO(b"first"), "image/jpeg"))]
    resp1 = await client.post("/api/receipts/upload", files=files1)
    assert resp1.status_code == 201
    data1 = resp1.json()
    assert data1["total_uploaded"] == 1
    first_date = data1["uploaded"][0]["receipt_date"]  # 2026-03-15 (mock 기본값)

    # 2차 업로드 (같은 날짜 OCR 결과)
    files2 = [("files", ("second.jpg", BytesIO(b"second"), "image/jpeg"))]
    with patch("app.routers.receipts.delete_image", return_value=True):
        resp2 = await client.post("/api/receipts/upload", files=files2)
    assert resp2.status_code == 201
    data2 = resp2.json()
    assert data2["total_uploaded"] == 1
    assert data2["replaced_count"] == 1

    # DB에 같은 날짜 영수증이 1건만 존재하는지 확인
    result = await db_session.execute(
        select(Receipt).where(
            Receipt.receipt_date == date.fromisoformat(first_date)
        )
    )
    receipts = result.scalars().all()
    assert len(receipts) == 1


@pytest.mark.asyncio
async def test_replaced_receipt_image_deleted(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """교체 시 delete_image 호출 확인."""
    # 1차 업로드
    files1 = [("files", ("old.jpg", BytesIO(b"old"), "image/jpeg"))]
    resp1 = await client.post("/api/receipts/upload", files=files1)
    assert resp1.status_code == 201
    old_image_path = resp1.json()["uploaded"][0]["image_path"]

    # 2차 업로드 (같은 날짜 OCR 결과) — delete_image mock
    files2 = [("files", ("new.jpg", BytesIO(b"new"), "image/jpeg"))]
    with patch("app.routers.receipts.delete_image", return_value=True) as mock_del:
        resp2 = await client.post("/api/receipts/upload", files=files2)
    assert resp2.status_code == 201

    # delete_image가 기존 이미지 경로로 호출되었는지 확인
    mock_del.assert_called_once_with(old_image_path)
