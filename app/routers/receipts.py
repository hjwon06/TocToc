"""영수증 CRUD 라우터 + 페이지 라우터.

A4 비즈니스 에이전트 소유 파일.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from math import ceil

from fastapi import APIRouter, Depends, Form, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import Receipt
from app.services.image import (
    compress_image,
    create_thumbnail,
    delete_image,
    get_image_url,
    get_thumbnail_url,
)
from app.services.invoice import AMOUNT_CAP, generate_invoice
from app.services.ocr import OcrResult, extract_receipt_data
from skills.upload_skill import save_upload, validate_file_size

logger = logging.getLogger(__name__)

MAX_UPLOAD_FILES = 20
OCR_CONCURRENCY = 5

templates = Jinja2Templates(directory="app/templates")

# ── API 라우터 ──────────────────────────────────────

router = APIRouter(prefix="/api/receipts", tags=["receipts"])


def _receipt_to_dict(receipt: Receipt) -> dict:
    """Receipt 모델을 딕셔너리로 변환."""
    raw_amount = receipt.amount
    capped = min(raw_amount, AMOUNT_CAP) if raw_amount and raw_amount > 0 else raw_amount
    return {
        "id": receipt.id,
        "image_path": receipt.image_path,
        "image_url": get_image_url(receipt.image_path),
        "thumbnail_url": get_thumbnail_url(receipt.image_path),
        "receipt_date": receipt.receipt_date.isoformat() if receipt.receipt_date else None,
        "amount": capped,
        "amount_raw": raw_amount,
        "is_manual": receipt.is_manual,
        "ocr_raw": receipt.ocr_raw,
        "created_at": receipt.created_at.isoformat() if receipt.created_at else None,
        "updated_at": receipt.updated_at.isoformat() if receipt.updated_at else None,
    }


async def _find_duplicates_by_date(
    db: AsyncSession,
    target_date: date,
    exclude_id: int | None = None,
) -> list[Receipt]:
    """같은 날짜의 기존 영수증 조회 (중복 교체용).

    receipt_date=NULL인 건은 = 비교에서 자동 제외된다.
    """
    query = select(Receipt).where(Receipt.receipt_date == target_date)
    if exclude_id is not None:
        query = query.where(Receipt.id != exclude_id)
    result = await db.execute(query)
    return list(result.scalars().all())


async def _delete_duplicates(
    db: AsyncSession,
    duplicates: list[Receipt],
) -> int:
    """중복 영수증 삭제 (이미지 + DB)."""
    count = 0
    for dup in duplicates:
        delete_image(dup.image_path)
        await db.delete(dup)
        count += 1
    return count


@router.post("/{receipt_id}/retry-ocr")
async def retry_ocr(
    receipt_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """영수증 OCR 재시도."""
    result = await db.execute(select(Receipt).where(Receipt.id == receipt_id))
    receipt = result.scalar_one_or_none()
    if not receipt:
        return JSONResponse(content={"error": "영수증을 찾을 수 없습니다."}, status_code=404)

    try:
        ocr_result = await extract_receipt_data(receipt.image_path)
    except Exception as e:
        logger.error("재OCR 실패: %s — %s", receipt_id, e)
        ocr_result = None

    if ocr_result and ocr_result.success:
        ocr_date = ocr_result.receipt_date
        today = date.today()
        date_min = date(today.year, today.month, 1)
        if ocr_date and ocr_date < date_min:
            logger.warning("재OCR 날짜 비정상: %s (%s) → 미분류", receipt_id, ocr_date)
            ocr_date = None

        # 같은 날짜 기존 건 교체
        if ocr_date:
            duplicates = await _find_duplicates_by_date(db, ocr_date, exclude_id=receipt.id)
            if duplicates:
                removed = await _delete_duplicates(db, duplicates)
                logger.info("재OCR 중복 교체: %s건 삭제 (날짜=%s)", removed, ocr_date)

        receipt.receipt_date = ocr_date
        receipt.amount = ocr_result.amount
        receipt.is_manual = ocr_date is None
        receipt.ocr_raw = ocr_result.raw_text
    else:
        receipt.ocr_raw = ocr_result.raw_text if ocr_result else "OCR 재시도 실패"

    await db.flush()
    await db.refresh(receipt)

    is_htmx = request.headers.get("HX-Request") == "true"
    if is_htmx:
        if not receipt.is_manual and receipt.receipt_date:
            # 완전 성공 → 카드 제거
            amt_text = f"₩{receipt.amount:,}" if receipt.amount else ""
            return HTMLResponse(
                '<div class="bg-green-50 text-green-700 rounded-lg p-3 text-sm">'
                f'✓ OCR 성공 — {receipt.receipt_date}, {amt_text}'
                '</div>'
            )
        # 실패 또는 날짜 비정상 → 카드 유지 + 안내
        msg = "날짜를 인식하지 못했습니다. 수동으로 입력해주세요." if ocr_result and ocr_result.success else "OCR 실패"
        return HTMLResponse(
            f'<div id="unclassified-{receipt.id}" class="bg-white rounded-lg shadow-sm p-4">'
            f'<div class="bg-yellow-50 text-yellow-700 rounded-lg p-3 text-sm mb-2">{msg}</div>'
            f'<a href="/receipts/{receipt.id}" class="text-blue-600 text-sm">수동 입력하기 →</a>'
            '</div>'
        )

    return JSONResponse(content=_receipt_to_dict(receipt))


@router.post("/upload")
async def upload_receipts(
    request: Request,
    files: list[UploadFile],
    db: AsyncSession = Depends(get_db),
) -> Response:
    """영수증 이미지 업로드 + OCR 처리.

    여러 장 업로드 가능. 각 파일에 대해:
    1. 파일 크기 검증
    2. 파일 저장 (upload_skill)
    3. 썸네일 생성
    4. OCR 추출
    5. DB 저장
    """
    if len(files) > MAX_UPLOAD_FILES:
        msg = f"한 번에 최대 {MAX_UPLOAD_FILES}장까지 업로드할 수 있습니다."
        is_htmx = request.headers.get("HX-Request") == "true"
        if is_htmx:
            return templates.TemplateResponse(
                request=request,
                name="partials/receipt_list.html",
                context={"receipts": [], "errors": [msg], "is_upload_result": True},
            )
        return JSONResponse(content={"error": msg}, status_code=400)

    results: list[dict] = []
    errors: list[str] = []

    # ── Phase 1: 파일 저장 + 썸네일 (순차, 빠름) ──
    saved: list[tuple[str, str]] = []  # (filename, image_path)
    for file in files:
        try:
            if not await validate_file_size(file, settings.MAX_FILE_SIZE_MB):
                errors.append(
                    f"{file.filename}: 파일 크기 초과 ({settings.MAX_FILE_SIZE_MB}MB 제한)"
                )
                continue
            try:
                image_path = await save_upload(file, settings.UPLOAD_DIR)
            except ValueError as e:
                errors.append(f"{file.filename}: {e}")
                continue
            except IOError as e:
                errors.append(f"{file.filename}: {e}")
                continue
            compress_image(image_path)
            create_thumbnail(image_path)
            saved.append((file.filename or "unknown", image_path))
        except Exception as e:
            logger.error("파일 저장 실패: %s — %s", file.filename, e)
            errors.append(f"{file.filename}: 처리 중 오류 발생")

    # ── Phase 2: 병렬 OCR (세마포어로 동시 5개 제한) ──
    semaphore = asyncio.Semaphore(OCR_CONCURRENCY)

    async def _ocr_task(image_path: str) -> OcrResult | None:
        async with semaphore:
            try:
                return await extract_receipt_data(image_path)
            except Exception as e:
                logger.error("OCR 실패: %s — %s", image_path, e)
                return None

    ocr_results = await asyncio.gather(*[_ocr_task(path) for _, path in saved])

    # ── Phase 3: DB 저장 (같은 날짜 기존 건 교체) ──
    today = date.today()
    date_min = date(today.year, today.month, 1)
    replaced_count = 0

    for (filename, image_path), ocr_result in zip(saved, ocr_results):
        if ocr_result and ocr_result.success:
            ocr_date = ocr_result.receipt_date
            # 날짜가 당월 이전이면 OCR 오류로 판단 → 미분류
            if ocr_date and ocr_date < date_min:
                logger.warning("OCR 날짜 비정상: %s (%s) → 미분류", filename, ocr_date)
                ocr_date = None

            # 같은 날짜 기존 건 교체
            if ocr_date:
                duplicates = await _find_duplicates_by_date(db, ocr_date)
                if duplicates:
                    removed = await _delete_duplicates(db, duplicates)
                    replaced_count += removed
                    logger.info("업로드 중복 교체: %s건 삭제 (날짜=%s)", removed, ocr_date)

            receipt = Receipt(
                image_path=image_path,
                receipt_date=ocr_date,
                amount=ocr_result.amount,
                is_manual=ocr_date is None,
                ocr_raw=ocr_result.raw_text,
            )
        else:
            receipt = Receipt(
                image_path=image_path,
                receipt_date=None,
                amount=None,
                is_manual=True,
                ocr_raw=ocr_result.raw_text if ocr_result else "OCR 처리 실패",
            )
        db.add(receipt)
        await db.flush()
        results.append(_receipt_to_dict(receipt))

    # HX-Request이면 partial HTML 반환
    is_htmx = request.headers.get("HX-Request") == "true"
    if is_htmx:
        return templates.TemplateResponse(
            request=request,
            name="partials/receipt_list.html",
            context={
                "receipts": results,
                "errors": errors,
                "is_upload_result": True,
                "replaced_count": replaced_count,
            },
        )

    return JSONResponse(
        content={
            "uploaded": results,
            "errors": errors,
            "total_uploaded": len(results),
            "replaced_count": replaced_count,
        },
        status_code=201 if results else 400,
    )


@router.get("/")
async def list_receipts(
    request: Request,
    month: str | None = Query(default=None, description="월 필터 (YYYY-MM)"),
    sort: str = Query(default="date_asc", description="정렬"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """영수증 목록 조회 (페이지네이션 + 필터 + 정렬)."""
    # 기본 쿼리
    query = select(Receipt)
    count_query = select(func.count(Receipt.id))

    # 월 필터
    if month:
        try:
            year, mon = month.split("-")
            start_date = date(int(year), int(mon), 1)
            if int(mon) == 12:
                end_date = date(int(year) + 1, 1, 1)
            else:
                end_date = date(int(year), int(mon) + 1, 1)
            query = query.where(
                Receipt.receipt_date >= start_date,
                Receipt.receipt_date < end_date,
            )
            count_query = count_query.where(
                Receipt.receipt_date >= start_date,
                Receipt.receipt_date < end_date,
            )
        except (ValueError, IndexError):
            pass  # 잘못된 형식 무시

    # 정렬
    if sort == "date_asc":
        query = query.order_by(Receipt.receipt_date.asc().nullslast(), Receipt.created_at.asc())
    elif sort == "amount_desc":
        query = query.order_by(Receipt.amount.desc().nullslast(), Receipt.created_at.desc())
    elif sort == "amount_asc":
        query = query.order_by(Receipt.amount.asc().nullslast(), Receipt.created_at.asc())
    else:  # date_desc (기본)
        query = query.order_by(Receipt.receipt_date.desc().nullsfirst(), Receipt.created_at.desc())

    # 전체 건수
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0
    total_pages = ceil(total / size) if total > 0 else 1

    # 페이지네이션
    offset = (page - 1) * size
    query = query.offset(offset).limit(size)

    result = await db.execute(query)
    receipts = result.scalars().all()
    receipt_dicts = [_receipt_to_dict(r) for r in receipts]

    # HX-Request이면 partial HTML 반환
    is_htmx = request.headers.get("HX-Request") == "true"
    if is_htmx:
        return templates.TemplateResponse(
            request=request,
            name="partials/receipt_list.html",
            context={
                "receipts": receipt_dicts,
                "page": page,
                "size": size,
                "total": total,
                "total_pages": total_pages,
            },
        )

    return JSONResponse(
        content={
            "items": receipt_dicts,
            "page": page,
            "size": size,
            "total": total,
            "total_pages": total_pages,
        }
    )


@router.get("/invoice-preview")
async def invoice_preview(
    request: Request,
    month: str = Query(description="월 (YYYY-MM)"),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """인보이스 미리보기 — HTMX partial."""
    try:
        year_str, mon_str = month.split("-")
        year, mon = int(year_str), int(mon_str)
        start_date = date(year, mon, 1)
        end_date = date(year + 1, 1, 1) if mon == 12 else date(year, mon + 1, 1)
    except (ValueError, IndexError):
        return HTMLResponse("<p class='text-red-500'>잘못된 월 형식입니다.</p>")

    from app.services.invoice import AMOUNT_CAP

    query = (
        select(Receipt)
        .where(
            (Receipt.receipt_date >= start_date) & (Receipt.receipt_date < end_date)
        )
        .order_by(Receipt.receipt_date.asc().nullslast())
    )
    result = await db.execute(query)
    receipts = result.scalars().all()

    preview_data = []
    total_capped = 0
    for r in receipts:
        amt = r.amount if r.amount and r.amount > 0 else 0
        capped = min(amt, AMOUNT_CAP)
        total_capped += capped
        preview_data.append({
            "date": r.receipt_date.isoformat() if r.receipt_date else None,
            "capped_amount": capped,
        })

    return templates.TemplateResponse(
        request=request,
        name="partials/invoice_preview.html",
        context={
            "receipts": preview_data,
            "month": f"{year}-{mon:02d}",
            "total_capped": total_capped,
        },
    )


@router.get("/export", response_model=None)
async def export_invoice(
    month: str = Query(description="월 (YYYY-MM)"),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse | JSONResponse:
    """월별 식비 인보이스 DOCX 다운로드."""
    try:
        year_str, mon_str = month.split("-")
        year, mon = int(year_str), int(mon_str)
        start_date = date(year, mon, 1)
        if mon == 12:
            end_date = date(year + 1, 1, 1)
        else:
            end_date = date(year, mon + 1, 1)
    except (ValueError, IndexError):
        return JSONResponse(
            content={"error": "잘못된 월 형식입니다. (YYYY-MM)"},
            status_code=400,
        )

    # 해당 월 영수증 조회
    query = (
        select(Receipt)
        .where(
            (Receipt.receipt_date >= start_date) & (Receipt.receipt_date < end_date)
            | Receipt.receipt_date.is_(None)
        )
        .order_by(Receipt.receipt_date.asc().nullslast())
    )
    result = await db.execute(query)
    receipts = result.scalars().all()
    receipt_dicts = [_receipt_to_dict(r) for r in receipts]

    docx_stream = generate_invoice(receipt_dicts, year, mon)
    filename = f"invoice_{year}_{mon:02d}.docx"

    return StreamingResponse(
        docx_stream,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{receipt_id}")
async def get_receipt(
    receipt_id: int,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """영수증 상세 조회."""
    result = await db.execute(select(Receipt).where(Receipt.id == receipt_id))
    receipt = result.scalar_one_or_none()
    if not receipt:
        return JSONResponse(content={"error": "영수증을 찾을 수 없습니다."}, status_code=404)

    return JSONResponse(content=_receipt_to_dict(receipt))


@router.put("/{receipt_id}")
async def update_receipt(
    receipt_id: int,
    request: Request,
    receipt_date: str | None = Form(default=None),
    amount: int | None = Form(default=None),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """영수증 수정 (날짜, 금액)."""
    result = await db.execute(select(Receipt).where(Receipt.id == receipt_id))
    receipt = result.scalar_one_or_none()
    if not receipt:
        return JSONResponse(content={"error": "영수증을 찾을 수 없습니다."}, status_code=404)

    # 날짜 업데이트
    if receipt_date is not None:
        if receipt_date == "":
            receipt.receipt_date = None
        else:
            try:
                parsed_date = date.fromisoformat(receipt_date)
            except ValueError:
                return JSONResponse(
                    content={"error": "날짜 형식이 올바르지 않습니다. (YYYY-MM-DD)"},
                    status_code=400,
                )
            # 같은 날짜 기존 건 교체
            duplicates = await _find_duplicates_by_date(db, parsed_date, exclude_id=receipt.id)
            if duplicates:
                removed = await _delete_duplicates(db, duplicates)
                logger.info("수정 중복 교체: %s건 삭제 (날짜=%s)", removed, parsed_date)
            receipt.receipt_date = parsed_date

    # 금액 업데이트
    if amount is not None:
        receipt.amount = amount

    receipt.is_manual = receipt.receipt_date is None
    await db.flush()
    await db.refresh(receipt)

    is_htmx = request.headers.get("HX-Request") == "true"
    if is_htmx:
        response = templates.TemplateResponse(
            request=request,
            name="partials/receipt_card.html",
            context={
                "receipt": _receipt_to_dict(receipt),
            },
        )
        response.headers["HX-Trigger"] = "receipt-updated"
        return response

    return JSONResponse(content=_receipt_to_dict(receipt))


@router.delete("/{receipt_id}")
async def delete_receipt(
    receipt_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """영수증 삭제 (이미지 파일도 삭제)."""
    result = await db.execute(select(Receipt).where(Receipt.id == receipt_id))
    receipt = result.scalar_one_or_none()
    if not receipt:
        return JSONResponse(content={"error": "영수증을 찾을 수 없습니다."}, status_code=404)

    # 이미지 파일 삭제
    delete_image(receipt.image_path)

    # DB 삭제
    await db.delete(receipt)
    await db.flush()

    is_htmx = request.headers.get("HX-Request") == "true"
    if is_htmx:
        response = HTMLResponse(content="", status_code=200)
        response.headers["HX-Trigger"] = "receipt-deleted"
        return response

    return JSONResponse(content={"message": "삭제되었습니다."})


# ── 페이지 라우터 ───────────────────────────────────

page_router = APIRouter(tags=["pages"])


@page_router.get("/", response_class=HTMLResponse)
async def index_page(request: Request) -> HTMLResponse:
    """메인 목록 페이지."""
    return templates.TemplateResponse(request=request, name="index.html")


@page_router.get("/unclassified", response_class=HTMLResponse)
async def unclassified_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """미분류 영수증 목록 페이지."""
    query = (
        select(Receipt)
        .where(Receipt.receipt_date.is_(None))
        .order_by(Receipt.created_at.desc())
    )
    result = await db.execute(query)
    receipts = result.scalars().all()
    receipt_dicts = [_receipt_to_dict(r) for r in receipts]

    return templates.TemplateResponse(
        request=request,
        name="unclassified.html",
        context={"receipts": receipt_dicts, "total": len(receipt_dicts)},
    )


@router.post("/retry-all-ocr")
async def retry_all_ocr(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """미분류 영수증 전체 재OCR."""
    query = (
        select(Receipt)
        .where(Receipt.receipt_date.is_(None))
        .order_by(Receipt.created_at.desc())
    )
    result = await db.execute(query)
    receipts = list(result.scalars().all())

    if not receipts:
        return HTMLResponse(
            '<div class="text-center py-16 text-gray-400">'
            '<p class="text-base">미분류 영수증이 없습니다</p></div>'
        )

    # Phase 1: 병렬 OCR (결과만 수집)
    semaphore = asyncio.Semaphore(OCR_CONCURRENCY)

    async def _ocr_one(receipt: Receipt) -> tuple[Receipt, OcrResult | None]:
        async with semaphore:
            try:
                ocr_result = await extract_receipt_data(receipt.image_path)
            except Exception as e:
                logger.error("재OCR 실패: %s — %s", receipt.id, e)
                return receipt, None
            return receipt, ocr_result

    ocr_pairs = await asyncio.gather(*[_ocr_one(r) for r in receipts])

    # Phase 2: 순차 DB 업데이트 (중복 교체 포함, race condition 방지)
    today = date.today()
    date_min = date(today.year, today.month, 1)

    for receipt, ocr_result in ocr_pairs:
        if ocr_result and ocr_result.success:
            ocr_date = ocr_result.receipt_date
            if ocr_date and ocr_date < date_min:
                logger.warning("전체재OCR 날짜 비정상: %s (%s) → 미분류", receipt.id, ocr_date)
                ocr_date = None

            # 같은 날짜 기존 건 교체
            if ocr_date:
                duplicates = await _find_duplicates_by_date(db, ocr_date, exclude_id=receipt.id)
                if duplicates:
                    removed = await _delete_duplicates(db, duplicates)
                    logger.info("전체재OCR 중복 교체: %s건 삭제 (날짜=%s)", removed, ocr_date)

            receipt.receipt_date = ocr_date
            receipt.amount = ocr_result.amount
            receipt.is_manual = ocr_date is None
            receipt.ocr_raw = ocr_result.raw_text
        else:
            receipt.ocr_raw = ocr_result.raw_text if ocr_result else "OCR 재시도 실패"

    await db.flush()

    # 남은 미분류 조회
    result2 = await db.execute(
        select(Receipt)
        .where(Receipt.receipt_date.is_(None))
        .order_by(Receipt.created_at.desc())
    )
    remaining = result2.scalars().all()
    remaining_dicts = [_receipt_to_dict(r) for r in remaining]
    success_count = len(receipts) - len(remaining_dicts)

    return templates.TemplateResponse(
        request=request,
        name="unclassified.html",
        context={
            "receipts": remaining_dicts,
            "total": len(remaining_dicts),
            "success_count": success_count,
        },
    )


@page_router.get("/upload", response_class=HTMLResponse)
async def upload_page(request: Request) -> HTMLResponse:
    """업로드 페이지."""
    return templates.TemplateResponse(
        request=request,
        name="upload.html",
        context={"max_file_size": settings.MAX_FILE_SIZE_MB},
    )


@page_router.get("/invoice", response_class=HTMLResponse)
async def invoice_page(request: Request) -> HTMLResponse:
    """인보이스 페이지."""
    today = date.today()
    current_month = f"{today.year}-{today.month:02d}"
    return templates.TemplateResponse(
        request=request,
        name="invoice.html",
        context={"current_month": current_month},
    )


@page_router.get("/receipts/{receipt_id}", response_class=HTMLResponse)
async def detail_page(
    receipt_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """영수증 상세/수정 페이지."""
    result = await db.execute(select(Receipt).where(Receipt.id == receipt_id))
    receipt = result.scalar_one_or_none()
    if not receipt:
        return templates.TemplateResponse(
            request=request,
            name="detail.html",
            context={"receipt": None, "error": "영수증을 찾을 수 없습니다."},
            status_code=404,
        )

    return templates.TemplateResponse(
        request=request,
        name="detail.html",
        context={"receipt": _receipt_to_dict(receipt)},
    )
