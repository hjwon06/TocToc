"""E2E 테스트 — HTMX partial 응답 검증.

QA 에이전트 소유.
"""

from __future__ import annotations

from io import BytesIO

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_htmx_upload_returns_partial(client: AsyncClient) -> None:
    """HX-Request 헤더 → HTML partial 응답."""
    files = [("files", ("r.jpg", BytesIO(b"img"), "image/jpeg"))]
    resp = await client.post(
        "/api/receipts/upload",
        files=files,
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "등록 완료" in resp.text


@pytest.mark.asyncio
async def test_htmx_list_returns_partial(client: AsyncClient) -> None:
    """목록 HTMX 요청 → partial 응답."""
    resp = await client.get(
        "/api/receipts/",
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


@pytest.mark.asyncio
async def test_htmx_delete_triggers_event(client: AsyncClient) -> None:
    """삭제 → HX-Trigger 헤더 확인."""
    # 먼저 업로드
    files = [("files", ("r.jpg", BytesIO(b"img"), "image/jpeg"))]
    resp = await client.post("/api/receipts/upload", files=files)
    rid = resp.json()["uploaded"][0]["id"]

    # HTMX 삭제
    resp = await client.delete(
        f"/api/receipts/{rid}",
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    assert resp.headers.get("HX-Trigger") == "receipt-deleted"


@pytest.mark.asyncio
async def test_json_fallback_without_htmx(client: AsyncClient) -> None:
    """HX-Request 없으면 JSON 반환."""
    resp = await client.get("/api/receipts/")
    assert resp.status_code == 200
    assert "application/json" in resp.headers["content-type"]
    data = resp.json()
    assert "items" in data
    assert "total" in data
