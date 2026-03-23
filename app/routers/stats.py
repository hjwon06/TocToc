"""통계 API + 페이지 라우터.

A4 비즈니스 에이전트 소유 파일.
"""

from __future__ import annotations

import logging
from datetime import date

from dateutil.relativedelta import relativedelta
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import extract, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Receipt

logger = logging.getLogger(__name__)

templates = Jinja2Templates(directory="app/templates")

# ── API 라우터 ──────────────────────────────────────

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("/")
async def get_monthly_stats(
    month: str | None = Query(default=None, description="월 (YYYY-MM)"),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """특정 월의 통계 (건수, 합계, 평균, 최소, 최대)."""
    # 기본값: 이번 달
    if month:
        try:
            year, mon = month.split("-")
            target_year, target_month = int(year), int(mon)
        except (ValueError, IndexError):
            return JSONResponse(
                content={"error": "잘못된 월 형식입니다. (YYYY-MM)"},
                status_code=400,
            )
    else:
        today = date.today()
        target_year, target_month = today.year, today.month

    start_date = date(target_year, target_month, 1)
    if target_month == 12:
        end_date = date(target_year + 1, 1, 1)
    else:
        end_date = date(target_year, target_month + 1, 1)

    # 분류된 건 (receipt_date가 있는 건)
    classified_query = select(
        func.count(Receipt.id).label("count"),
        func.coalesce(func.sum(Receipt.amount), 0).label("total"),
        func.coalesce(func.avg(Receipt.amount), 0).label("avg"),
        func.min(Receipt.amount).label("min"),
        func.max(Receipt.amount).label("max"),
    ).where(
        Receipt.receipt_date >= start_date,
        Receipt.receipt_date < end_date,
        Receipt.amount.is_not(None),
    )

    result = await db.execute(classified_query)
    row = result.one()

    # 미분류 건수
    unclassified_query = select(
        func.count(Receipt.id)
    ).where(
        Receipt.receipt_date.is_(None),
    )
    unclassified_result = await db.execute(unclassified_query)
    unclassified_count = unclassified_result.scalar() or 0

    return JSONResponse(
        content={
            "month": f"{target_year}-{target_month:02d}",
            "count": row.count,
            "total": int(row.total),
            "avg": round(float(row.avg)),
            "min": row.min,
            "max": row.max,
            "unclassified": unclassified_count,
        }
    )


@router.get("/monthly")
async def get_monthly_trend(
    months: int = Query(default=6, ge=1, le=12),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """최근 N개월 추이 (월별 건수 + 합계)."""
    today = date.today()
    start_date = date(today.year, today.month, 1) - relativedelta(months=months - 1)

    query = (
        select(
            extract("year", Receipt.receipt_date).label("year"),
            extract("month", Receipt.receipt_date).label("month"),
            func.count(Receipt.id).label("count"),
            func.coalesce(func.sum(Receipt.amount), 0).label("total"),
        )
        .where(
            Receipt.receipt_date >= start_date,
            Receipt.receipt_date.is_not(None),
        )
        .group_by(
            extract("year", Receipt.receipt_date),
            extract("month", Receipt.receipt_date),
        )
        .order_by(
            extract("year", Receipt.receipt_date),
            extract("month", Receipt.receipt_date),
        )
    )

    result = await db.execute(query)
    rows = result.all()

    # 빈 달 채우기
    trend: list[dict] = []
    data_map: dict[str, dict] = {}
    for row in rows:
        key = f"{int(row.year)}-{int(row.month):02d}"
        data_map[key] = {
            "month": key,
            "count": row.count,
            "total": int(row.total),
        }

    current = start_date
    for _ in range(months):
        key = f"{current.year}-{current.month:02d}"
        if key in data_map:
            trend.append(data_map[key])
        else:
            trend.append({"month": key, "count": 0, "total": 0})
        current = current + relativedelta(months=1)

    return JSONResponse(content={"trend": trend})


# ── 페이지 라우터 ───────────────────────────────────

page_router = APIRouter(tags=["pages"])


@page_router.get("/stats", response_class=HTMLResponse)
async def stats_page(request: Request) -> HTMLResponse:
    """통계 페이지."""
    today = date.today()
    current_month = f"{today.year}-{today.month:02d}"
    return templates.TemplateResponse(
        request=request,
        name="stats.html",
        context={"current_month": current_month},
    )
