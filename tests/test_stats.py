"""통계 라우터 테스트.

A4 비즈니스 에이전트 소유 파일.
"""

from __future__ import annotations

from datetime import date

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Receipt


pytestmark = pytest.mark.asyncio


# ── 헬퍼 ────────────────────────────────────────────


async def _create_receipt(
    db: AsyncSession,
    receipt_date: date | None = None,
    amount: int | None = None,
    image_path: str = "static/uploads/test.jpg",
) -> Receipt:
    """테스트용 영수증 생성."""
    receipt = Receipt(
        image_path=image_path,
        receipt_date=receipt_date,
        amount=amount,
        is_manual=False,
    )
    db.add(receipt)
    await db.flush()
    return receipt


# ── 월별 통계 테스트 ─────────────────────────────────


async def test_monthly_stats_empty(client: AsyncClient) -> None:
    """데이터 없을 때 통계."""
    resp = await client.get("/api/stats/?month=2026-03")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 0
    assert data["total"] == 0
    assert data["avg"] == 0


async def test_monthly_stats_with_data(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """데이터 있을 때 월별 통계."""
    await _create_receipt(
        db_session, receipt_date=date(2026, 3, 1), amount=10000,
        image_path="static/uploads/s1.jpg",
    )
    await _create_receipt(
        db_session, receipt_date=date(2026, 3, 15), amount=20000,
        image_path="static/uploads/s2.jpg",
    )
    await _create_receipt(
        db_session, receipt_date=date(2026, 3, 20), amount=30000,
        image_path="static/uploads/s3.jpg",
    )
    await db_session.commit()

    resp = await client.get("/api/stats/?month=2026-03")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 3
    assert data["total"] == 60000
    assert data["avg"] == 20000
    assert data["min"] == 10000
    assert data["max"] == 30000


async def test_monthly_stats_unclassified(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """미분류(receipt_date=NULL) 건수."""
    await _create_receipt(
        db_session, receipt_date=None, amount=None,
        image_path="static/uploads/u1.jpg",
    )
    await _create_receipt(
        db_session, receipt_date=None, amount=None,
        image_path="static/uploads/u2.jpg",
    )
    await db_session.commit()

    resp = await client.get("/api/stats/?month=2026-03")
    assert resp.status_code == 200
    data = resp.json()
    assert data["unclassified"] == 2


async def test_monthly_stats_invalid_month(client: AsyncClient) -> None:
    """잘못된 월 형식."""
    resp = await client.get("/api/stats/?month=invalid")
    assert resp.status_code == 400


async def test_monthly_stats_default_month(client: AsyncClient) -> None:
    """month 파라미터 생략 시 이번 달."""
    resp = await client.get("/api/stats/")
    assert resp.status_code == 200
    data = resp.json()
    today = date.today()
    assert data["month"] == f"{today.year}-{today.month:02d}"


# ── 6개월 추이 테스트 ────────────────────────────────


async def test_monthly_trend_empty(client: AsyncClient) -> None:
    """데이터 없을 때 추이 — 6개월 빈 배열."""
    resp = await client.get("/api/stats/monthly?months=6")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["trend"]) == 6
    for item in data["trend"]:
        assert item["count"] == 0
        assert item["total"] == 0


async def test_monthly_trend_with_data(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """데이터 있을 때 추이."""
    today = date.today()
    await _create_receipt(
        db_session,
        receipt_date=date(today.year, today.month, 1),
        amount=15000,
        image_path="static/uploads/t1.jpg",
    )
    await db_session.commit()

    resp = await client.get("/api/stats/monthly?months=6")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["trend"]) == 6

    # 이번 달 데이터 확인
    current_month = f"{today.year}-{today.month:02d}"
    current_data = next(
        (t for t in data["trend"] if t["month"] == current_month), None
    )
    assert current_data is not None
    assert current_data["count"] == 1
    assert current_data["total"] == 15000


# ── 통계 페이지 테스트 ───────────────────────────────


async def test_stats_page(client: AsyncClient) -> None:
    """통계 페이지 렌더링."""
    resp = await client.get("/stats")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
