"""통합 테스트 — CRUD 플로우 (DB 정합성 검증).

QA 에이전트 소유.
"""

from __future__ import annotations

from io import BytesIO

import pytest
from httpx import AsyncClient


async def _upload_one(client: AsyncClient) -> dict:
    """테스트 헬퍼: 영수증 1건 업로드 후 dict 반환."""
    files = [("files", ("test.jpg", BytesIO(b"img"), "image/jpeg"))]
    resp = await client.post("/api/receipts/upload", files=files)
    assert resp.status_code == 201
    return resp.json()["uploaded"][0]


@pytest.mark.asyncio
async def test_update_persists(client: AsyncClient) -> None:
    """수정 후 재조회 시 값 반영 확인."""
    receipt = await _upload_one(client)
    rid = receipt["id"]

    # 수정
    resp = await client.put(
        f"/api/receipts/{rid}",
        data={"receipt_date": "2026-01-01", "amount": "99000"},
    )
    assert resp.status_code == 200

    # 재조회
    resp = await client.get(f"/api/receipts/{rid}")
    assert resp.status_code == 200
    updated = resp.json()
    assert updated["receipt_date"] == "2026-01-01"
    assert updated["amount_raw"] == 99000
    assert updated["is_manual"] is True


@pytest.mark.asyncio
async def test_delete_removes_record(client: AsyncClient) -> None:
    """삭제 후 재조회 시 404."""
    receipt = await _upload_one(client)
    rid = receipt["id"]

    resp = await client.delete(f"/api/receipts/{rid}")
    assert resp.status_code == 200

    resp = await client.get(f"/api/receipts/{rid}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_pagination_boundary(client: AsyncClient) -> None:
    """21건 등록 → page=2, size=20 시 1건 반환."""
    for _ in range(21):
        await _upload_one(client)

    resp = await client.get("/api/receipts/", params={"page": 1, "size": 20})
    data = resp.json()
    assert data["total"] == 21
    assert data["total_pages"] == 2
    assert len(data["items"]) == 20

    resp = await client.get("/api/receipts/", params={"page": 2, "size": 20})
    data = resp.json()
    assert len(data["items"]) == 1


@pytest.mark.asyncio
async def test_month_filter_accuracy(
    client: AsyncClient,
    mock_ocr,
) -> None:
    """이번 달/다른 달 데이터 혼합 → 필터 정확성."""
    from datetime import date as d

    from app.services.ocr import OcrResult

    today = d.today()

    # 이번 달 3건
    mock_ocr.return_value = OcrResult(
        receipt_date=d(today.year, today.month, 10), amount=8000, raw_text="this", success=True,
    )
    for _ in range(3):
        await _upload_one(client)

    # OCR 실패 2건 (미분류)
    mock_ocr.return_value = OcrResult(
        receipt_date=None, amount=None, raw_text="fail", success=False,
    )
    for _ in range(2):
        await _upload_one(client)

    # 이번 달 필터
    month_str = f"{today.year}-{today.month:02d}"
    resp = await client.get("/api/receipts/", params={"month": month_str})
    data = resp.json()
    assert data["total"] == 3

    # 전체 (필터 없음) → 5건
    resp = await client.get("/api/receipts/")
    data = resp.json()
    assert data["total"] == 5
