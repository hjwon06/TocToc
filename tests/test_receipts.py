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
    assert data["amount_raw"] == 15000


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
    assert data["amount_raw"] == 25000
    assert data["is_manual"] is False  # 날짜가 있으므로 미분류 아님


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
    assert data["uploaded"][0]["amount_raw"] == 12500
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


# ── 같은 날짜 영수증 교체 테스트 ──────────────────────


async def test_upload_replaces_same_date(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """같은 날짜 기존 건 있을 때 업로드 → 교체 확인."""
    # 기존 영수증 생성 (3/19)
    existing = await _create_receipt(
        db_session,
        receipt_date=date(2026, 3, 19),
        amount=5000,
        image_path="static/uploads/old.jpg",
    )
    await db_session.commit()
    existing_id = existing.id

    mock_ocr_result = OcrResult(
        receipt_date=date(2026, 3, 19),
        amount=12500,
        store_name="새 가게",
        raw_text="새 OCR 결과",
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
            return_value="static/uploads/new123.jpg",
        ),
        patch(
            "app.routers.receipts.create_thumbnail",
            return_value="static/uploads/new123_thumb.jpg",
        ),
        patch(
            "app.routers.receipts.extract_receipt_data",
            new_callable=AsyncMock,
            return_value=mock_ocr_result,
        ),
        patch("app.routers.receipts.delete_image", return_value=True) as mock_del,
    ):
        resp = await client.post(
            "/api/receipts/upload",
            files=[("files", ("new.jpg", io.BytesIO(b"fake"), "image/jpeg"))],
        )

    assert resp.status_code == 201
    data = resp.json()
    assert data["total_uploaded"] == 1
    assert data["replaced_count"] == 1
    mock_del.assert_called_once_with("static/uploads/old.jpg")

    # 기존 건이 삭제되었는지 확인
    resp2 = await client.get(f"/api/receipts/{existing_id}")
    assert resp2.status_code == 404


async def test_upload_null_date_no_replace(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """OCR 실패(날짜 NULL) → 교체 없이 새 건 추가."""
    existing = await _create_receipt(
        db_session,
        receipt_date=date(2026, 3, 19),
        amount=5000,
        image_path="static/uploads/old2.jpg",
    )
    await db_session.commit()

    mock_ocr_result = OcrResult(
        raw_text="OCR 실패",
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
            return_value="static/uploads/new456.jpg",
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
        patch("app.routers.receipts.delete_image", return_value=True) as mock_del,
    ):
        resp = await client.post(
            "/api/receipts/upload",
            files=[("files", ("new.jpg", io.BytesIO(b"fake"), "image/jpeg"))],
        )

    assert resp.status_code == 201
    data = resp.json()
    assert data["total_uploaded"] == 1
    assert data["replaced_count"] == 0
    mock_del.assert_not_called()

    # 기존 건이 여전히 존재
    resp2 = await client.get(f"/api/receipts/{existing.id}")
    assert resp2.status_code == 200


async def test_update_date_replaces_existing(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """수동 날짜 수정으로 중복 발생 → 기존 건 삭제."""
    existing = await _create_receipt(
        db_session,
        receipt_date=date(2026, 3, 10),
        amount=5000,
        image_path="static/uploads/will_delete.jpg",
    )
    target = await _create_receipt(
        db_session,
        receipt_date=date(2026, 3, 5),
        amount=8000,
        image_path="static/uploads/keep.jpg",
    )
    await db_session.commit()
    existing_id = existing.id

    with patch("app.routers.receipts.delete_image", return_value=True) as mock_del:
        resp = await client.put(
            f"/api/receipts/{target.id}",
            data={"receipt_date": "2026-03-10"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["receipt_date"] == "2026-03-10"
    mock_del.assert_called_once_with("static/uploads/will_delete.jpg")

    # 기존 건 삭제 확인
    resp2 = await client.get(f"/api/receipts/{existing_id}")
    assert resp2.status_code == 404


async def test_update_date_no_duplicate(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """중복 없는 날짜로 수정 → 정상 업데이트."""
    target = await _create_receipt(
        db_session,
        receipt_date=date(2026, 3, 5),
        amount=8000,
        image_path="static/uploads/target.jpg",
    )
    await db_session.commit()

    with patch("app.routers.receipts.delete_image", return_value=True) as mock_del:
        resp = await client.put(
            f"/api/receipts/{target.id}",
            data={"receipt_date": "2026-03-20"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["receipt_date"] == "2026-03-20"
    mock_del.assert_not_called()


async def test_upload_same_batch_duplicate(
    client: AsyncClient,
) -> None:
    """같은 배치에서 같은 날짜 2장 → 마지막 건만 남음."""
    mock_ocr_result = OcrResult(
        receipt_date=date(2026, 3, 19),
        amount=12500,
        store_name="가게",
        raw_text="OCR 결과",
        success=True,
    )

    call_count = 0

    async def _fake_save(file, upload_dir):
        nonlocal call_count
        call_count += 1
        return f"static/uploads/batch_{call_count}.jpg"

    with (
        patch(
            "app.routers.receipts.validate_file_size",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "app.routers.receipts.save_upload",
            new_callable=AsyncMock,
            side_effect=_fake_save,
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
        patch("app.routers.receipts.delete_image", return_value=True),
    ):
        resp = await client.post(
            "/api/receipts/upload",
            files=[
                ("files", ("a.jpg", io.BytesIO(b"fake1"), "image/jpeg")),
                ("files", ("b.jpg", io.BytesIO(b"fake2"), "image/jpeg")),
            ],
        )

    assert resp.status_code == 201
    data = resp.json()
    assert data["total_uploaded"] == 2
    # 첫 번째 건이 두 번째 건에 의해 교체됨
    assert data["replaced_count"] >= 1

    # 목록 조회 → 같은 날짜는 1건만
    resp2 = await client.get("/api/receipts/?month=2026-03")
    data2 = resp2.json()
    march_19 = [
        r for r in data2["items"] if r["receipt_date"] == "2026-03-19"
    ]
    assert len(march_19) == 1
