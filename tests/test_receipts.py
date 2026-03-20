"""영수증 라우터 테스트.

A4 비즈니스 에이전트 소유 파일.
"""

from __future__ import annotations

import io
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Receipt
from app.services.ocr import OcrResult


pytestmark = pytest.mark.asyncio


# ── 헬퍼 ────────────────────────────────────────────


async def _create_receipt(
    db: AsyncSession,
    receipt_date: date | None = None,
    amount: int | None = None,
    image_path: str = "static/uploads/test.jpg",
    is_manual: bool = False,
    ocr_raw: str | None = None,
) -> Receipt:
    """테스트용 영수증 생성."""
    receipt = Receipt(
        image_path=image_path,
        receipt_date=receipt_date,
        amount=amount,
        is_manual=is_manual,
        ocr_raw=ocr_raw,
    )
    db.add(receipt)
    await db.flush()
    return receipt


# ── 목록 조회 테스트 ─────────────────────────────────


async def test_list_receipts_empty(client: AsyncClient) -> None:
    """영수증이 없을 때 빈 목록 반환."""
    resp = await client.get("/api/receipts/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["items"] == []
    assert data["total"] == 0
    assert data["page"] == 1


async def test_list_receipts_with_data(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """영수증 목록 조회."""
    await _create_receipt(db_session, receipt_date=date(2026, 3, 1), amount=10000)
    await _create_receipt(db_session, receipt_date=date(2026, 3, 15), amount=25000)
    await db_session.commit()

    resp = await client.get("/api/receipts/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert len(data["items"]) == 2


async def test_list_receipts_month_filter(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """월 필터 테스트."""
    await _create_receipt(db_session, receipt_date=date(2026, 3, 1), amount=10000)
    await _create_receipt(db_session, receipt_date=date(2026, 2, 15), amount=25000)
    await db_session.commit()

    resp = await client.get("/api/receipts/?month=2026-03")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["amount"] == 10000


async def test_list_receipts_pagination(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """페이지네이션 테스트."""
    for i in range(5):
        await _create_receipt(
            db_session,
            receipt_date=date(2026, 3, i + 1),
            amount=(i + 1) * 1000,
            image_path=f"static/uploads/test{i}.jpg",
        )
    await db_session.commit()

    resp = await client.get("/api/receipts/?page=1&size=2")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 5
    assert data["total_pages"] == 3
    assert len(data["items"]) == 2
    assert data["page"] == 1


async def test_list_receipts_htmx(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """HTMX 요청 시 HTML 응답."""
    await _create_receipt(db_session, receipt_date=date(2026, 3, 1), amount=10000)
    await db_session.commit()

    resp = await client.get(
        "/api/receipts/",
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")


# ── 상세 조회 테스트 ─────────────────────────────────


async def test_get_receipt(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """영수증 상세 조회."""
    receipt = await _create_receipt(
        db_session, receipt_date=date(2026, 3, 1), amount=15000
    )
    await db_session.commit()

    resp = await client.get(f"/api/receipts/{receipt.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == receipt.id
    assert data["amount"] == 15000


async def test_get_receipt_not_found(client: AsyncClient) -> None:
    """존재하지 않는 영수증 조회."""
    resp = await client.get("/api/receipts/9999")
    assert resp.status_code == 404


# ── 수정 테스트 ──────────────────────────────────────


async def test_update_receipt(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """영수증 수정."""
    receipt = await _create_receipt(
        db_session, receipt_date=date(2026, 3, 1), amount=10000
    )
    await db_session.commit()

    resp = await client.put(
        f"/api/receipts/{receipt.id}",
        data={"receipt_date": "2026-03-15", "amount": "25000"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["receipt_date"] == "2026-03-15"
    assert data["amount"] == 25000
    assert data["is_manual"] is True


async def test_update_receipt_invalid_date(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """잘못된 날짜 형식으로 수정 시 400."""
    receipt = await _create_receipt(
        db_session, receipt_date=date(2026, 3, 1), amount=10000
    )
    await db_session.commit()

    resp = await client.put(
        f"/api/receipts/{receipt.id}",
        data={"receipt_date": "invalid-date"},
    )
    assert resp.status_code == 400


async def test_update_receipt_not_found(client: AsyncClient) -> None:
    """존재하지 않는 영수증 수정."""
    resp = await client.put("/api/receipts/9999", data={"amount": "1000"})
    assert resp.status_code == 404


# ── 삭제 테스트 ──────────────────────────────────────


async def test_delete_receipt(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """영수증 삭제."""
    receipt = await _create_receipt(
        db_session, receipt_date=date(2026, 3, 1), amount=10000
    )
    await db_session.commit()

    with patch("app.routers.receipts.delete_image", return_value=True):
        resp = await client.delete(f"/api/receipts/{receipt.id}")
    assert resp.status_code == 200

    # 삭제 확인
    resp2 = await client.get(f"/api/receipts/{receipt.id}")
    assert resp2.status_code == 404


async def test_delete_receipt_not_found(client: AsyncClient) -> None:
    """존재하지 않는 영수증 삭제."""
    resp = await client.delete("/api/receipts/9999")
    assert resp.status_code == 404


# ── 업로드 테스트 ────────────────────────────────────


async def test_upload_receipt_success(client: AsyncClient) -> None:
    """영수증 업로드 성공 (OCR mock)."""
    mock_ocr_result = OcrResult(
        receipt_date=date(2026, 3, 19),
        amount=12500,
        store_name="테스트 가게",
        raw_text="테스트 OCR 결과",
        success=True,
    )

    with (
        patch(
            "app.routers.receipts.validate_file_size",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "app.routers.receipts.save_upload",
            new_callable=AsyncMock,
            return_value="static/uploads/test123.jpg",
        ),
        patch(
            "app.routers.receipts.create_thumbnail",
            return_value="static/uploads/test123_thumb.jpg",
        ),
        patch(
            "app.routers.receipts.extract_receipt_data",
            new_callable=AsyncMock,
            return_value=mock_ocr_result,
        ),
    ):
        file_content = b"fake image data"
        resp = await client.post(
            "/api/receipts/upload",
            files=[("files", ("test.jpg", io.BytesIO(file_content), "image/jpeg"))],
        )

    assert resp.status_code == 201
    data = resp.json()
    assert data["total_uploaded"] == 1
    assert data["uploaded"][0]["amount"] == 12500
    assert data["uploaded"][0]["receipt_date"] == "2026-03-19"


async def test_upload_receipt_ocr_failure(client: AsyncClient) -> None:
    """OCR 실패 시 수동 입력 플래그."""
    mock_ocr_result = OcrResult(
        raw_text="OCR 실패 메시지",
        success=False,
    )

    with (
        patch(
            "app.routers.receipts.validate_file_size",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "app.routers.receipts.save_upload",
            new_callable=AsyncMock,
            return_value="static/uploads/test456.jpg",
        ),
        patch(
            "app.routers.receipts.create_thumbnail",
            return_value=None,
        ),
        patch(
            "app.routers.receipts.extract_receipt_data",
            new_callable=AsyncMock,
            return_value=mock_ocr_result,
        ),
    ):
        file_content = b"fake image data"
        resp = await client.post(
            "/api/receipts/upload",
            files=[("files", ("test.jpg", io.BytesIO(file_content), "image/jpeg"))],
        )

    assert resp.status_code == 201
    data = resp.json()
    assert data["uploaded"][0]["is_manual"] is True
    assert data["uploaded"][0]["amount"] is None
    assert data["uploaded"][0]["receipt_date"] is None


async def test_upload_receipt_file_too_large(client: AsyncClient) -> None:
    """파일 크기 초과."""
    with patch(
        "app.routers.receipts.validate_file_size",
        new_callable=AsyncMock,
        return_value=False,
    ):
        file_content = b"fake image data"
        resp = await client.post(
            "/api/receipts/upload",
            files=[("files", ("test.jpg", io.BytesIO(file_content), "image/jpeg"))],
        )

    assert resp.status_code == 400
    data = resp.json()
    assert len(data["errors"]) == 1


async def test_upload_receipt_invalid_extension(client: AsyncClient) -> None:
    """허용되지 않는 확장자."""
    with (
        patch(
            "app.routers.receipts.validate_file_size",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "app.routers.receipts.save_upload",
            new_callable=AsyncMock,
            side_effect=ValueError("허용되지 않는 확장자: .txt"),
        ),
    ):
        file_content = b"not an image"
        resp = await client.post(
            "/api/receipts/upload",
            files=[("files", ("test.txt", io.BytesIO(file_content), "text/plain"))],
        )

    assert resp.status_code == 400
    data = resp.json()
    assert len(data["errors"]) == 1


# ── 페이지 라우터 테스트 ─────────────────────────────


async def test_index_page(client: AsyncClient) -> None:
    """메인 목록 페이지 렌더링."""
    resp = await client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")


async def test_upload_page(client: AsyncClient) -> None:
    """업로드 페이지 렌더링."""
    resp = await client.get("/upload")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")


async def test_detail_page(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """상세 페이지 렌더링."""
    receipt = await _create_receipt(
        db_session, receipt_date=date(2026, 3, 1), amount=10000
    )
    await db_session.commit()

    resp = await client.get(f"/receipts/{receipt.id}")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")


async def test_detail_page_not_found(client: AsyncClient) -> None:
    """존재하지 않는 영수증 상세 페이지."""
    resp = await client.get("/receipts/9999")
    assert resp.status_code == 404
