"""E2E 테스트 — 풀 플로우 시나리오.

QA 에이전트 소유.
"""

from __future__ import annotations

from datetime import date as d
from io import BytesIO

import pytest
from httpx import AsyncClient

from app.services.ocr import OcrResult


@pytest.mark.asyncio
async def test_upload_list_detail_edit_delete(
    client: AsyncClient,
) -> None:
    """업로드 → 목록 → 상세 → 수정 → 삭제 전체 시나리오."""
    # 1. 업로드
    files = [("files", ("receipt.jpg", BytesIO(b"img"), "image/jpeg"))]
    resp = await client.post("/api/receipts/upload", files=files)
    assert resp.status_code == 201
    receipt = resp.json()["uploaded"][0]
    rid = receipt["id"]

    # 2. 목록에서 확인
    resp = await client.get("/api/receipts/")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert any(r["id"] == rid for r in items)

    # 3. 상세 조회
    resp = await client.get(f"/api/receipts/{rid}")
    assert resp.status_code == 200
    assert resp.json()["amount_raw"] == 12000

    # 4. 수정
    resp = await client.put(
        f"/api/receipts/{rid}",
        data={"receipt_date": "2026-02-01", "amount": "55000"},
    )
    assert resp.status_code == 200
    updated = resp.json()
    assert updated["receipt_date"] == "2026-02-01"
    assert updated["amount_raw"] == 55000

    # 5. 삭제
    resp = await client.delete(f"/api/receipts/{rid}")
    assert resp.status_code == 200

    # 6. 삭제 확인
    resp = await client.get(f"/api/receipts/{rid}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_upload_then_stats_consistency(
    client: AsyncClient,
    mock_ocr,
) -> None:
    """업로드 후 통계 수치 정합성."""
    amounts = [15000, 25000, 35000]
    dates = [d(2026, 3, 20), d(2026, 3, 21), d(2026, 3, 22)]
    for a, dt in zip(amounts, dates):
        mock_ocr.return_value = OcrResult(
            receipt_date=dt,
            amount=a,
            raw_text="test",
            success=True,
        )
        files = [("files", ("r.jpg", BytesIO(b"x"), "image/jpeg"))]
        resp = await client.post("/api/receipts/upload", files=files)
        assert resp.status_code == 201

    # 통계 확인
    resp = await client.get("/api/stats/", params={"month": "2026-03"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 3
    assert data["total"] == sum(amounts)
