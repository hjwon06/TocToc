"""upload_skill 테스트."""

import shutil
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio  # noqa: F401 — ensure plugin is loaded

from skills.upload_skill import save_upload, validate_file_size

TEST_UPLOAD_DIR = "test_uploads_tmp"


@pytest.fixture(autouse=True)
def _cleanup_upload_dir():
    """테스트 후 임시 업로드 디렉토리 삭제."""
    yield
    if Path(TEST_UPLOAD_DIR).exists():
        shutil.rmtree(TEST_UPLOAD_DIR)


def _make_upload_file(
    filename: str, content: bytes = b"fake-image-data"
) -> AsyncMock:
    """테스트용 UploadFile mock 생성."""
    mock = AsyncMock()
    mock.filename = filename
    mock.read = AsyncMock(return_value=content)
    mock.seek = AsyncMock()
    return mock


# ── save_upload 테스트 ────────────────────────────────


@pytest.mark.asyncio
async def test_save_upload_success_jpg() -> None:
    """정상적인 jpg 파일 저장."""
    file = _make_upload_file("receipt.jpg", b"\xff\xd8\xff\xe0fake")
    result = await save_upload(file, TEST_UPLOAD_DIR)

    assert result.startswith(TEST_UPLOAD_DIR)
    assert result.endswith(".jpg")
    assert Path(result).exists()


@pytest.mark.asyncio
async def test_save_upload_success_png() -> None:
    """정상적인 png 파일 저장."""
    file = _make_upload_file("photo.png", b"\x89PNGfake")
    result = await save_upload(file, TEST_UPLOAD_DIR)

    assert result.endswith(".png")
    assert Path(result).exists()


@pytest.mark.asyncio
async def test_save_upload_reject_pdf() -> None:
    """허용되지 않는 확장자(pdf) 거부."""
    file = _make_upload_file("document.pdf")
    with pytest.raises(ValueError, match="허용되지 않는 확장자"):
        await save_upload(file, TEST_UPLOAD_DIR)


@pytest.mark.asyncio
async def test_save_upload_reject_no_extension() -> None:
    """확장자 없는 파일 거부."""
    file = _make_upload_file("noextension")
    with pytest.raises(ValueError, match="허용되지 않는 확장자"):
        await save_upload(file, TEST_UPLOAD_DIR)


@pytest.mark.asyncio
async def test_save_upload_heic() -> None:
    """heic 파일 허용."""
    file = _make_upload_file("photo.HEIC", b"heic-data")
    result = await save_upload(file, TEST_UPLOAD_DIR)
    assert result.endswith(".heic")


# ── validate_file_size 테스트 ─────────────────────────


@pytest.mark.asyncio
async def test_validate_file_size_within_limit() -> None:
    """제한 이내 파일."""
    small_content = b"x" * (1024 * 1024)  # 1 MB
    file = _make_upload_file("img.jpg", small_content)
    assert await validate_file_size(file, max_size_mb=10) is True


@pytest.mark.asyncio
async def test_validate_file_size_exceeds_limit() -> None:
    """제한 초과 파일."""
    big_content = b"x" * (11 * 1024 * 1024)  # 11 MB
    file = _make_upload_file("img.jpg", big_content)
    assert await validate_file_size(file, max_size_mb=10) is False


@pytest.mark.asyncio
async def test_validate_file_size_exact_limit() -> None:
    """정확히 제한 크기와 같은 파일 — 허용."""
    exact_content = b"x" * (10 * 1024 * 1024)  # 10 MB
    file = _make_upload_file("img.jpg", exact_content)
    assert await validate_file_size(file, max_size_mb=10) is True
