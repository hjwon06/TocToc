"""통합 테스트 — 통계 정합성.

QA 에이전트 소유.
"""

from __future__ import annotations

from datetime import date as d
from io import BytesIO

import pytest
from httpx import AsyncClient

from app.services.ocr import OcrResult


async def _upload_with_amount(
    client: AsyncClient,
    mock_ocr,
    amount: int,
    receipt_date: d | None = None,
) -> dict:
    """지정 금액으로 영수증 업로드."""
    mock_ocr.return_value = OcrResult(
        receipt_date=receipt_date or d(2026, 3, 15),
        amount=amount,
        raw_text="mock",
        success=True,
    )
    files = [("files", ("r.jpg", BytesIO(b"img"), "image/jpeg"))]
    resp = await client.post("/api/receipts/upload", files=files)
    assert resp.status_code == 201
    return resp.json()["uploaded"][0]


@pytest.mark.asyncio
async def test_stats_match_uploads(client: AsyncClient, mock_ocr) -> None:
    """5건 업로드 → 통계 합계 일치."""
    amounts = [10000, 20000, 30000, 15000, 25000]
    for a in amounts:
        await _upload_with_amount(client, mock_ocr, a)

    resp = await client.get("/api/stats/", params={"month": "2026-03"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == sum(amounts)
    assert data["count"] == 5


@pytest.mark.asyncio
async def test_stats_empty_month(client: AsyncClient, mock_ocr) -> None:
    """데이터 없는 월 → 0 반환."""
    # 3월 데이터만 있음
    await _upload_with_amount(client, mock_ocr, 10000, d(2026, 3, 1))

    # 4월 통계 조회
    resp = await client.get("/api/stats/", params={"month": "2026-04"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["count"] == 0
