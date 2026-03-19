"""파일 업로드 스킬 — 영수증 이미지 저장 유틸리티."""

import uuid
from pathlib import Path

from fastapi import UploadFile

ALLOWED_EXTENSIONS: set[str] = {"jpg", "jpeg", "png", "heic"}


async def save_upload(file: UploadFile, upload_dir: str) -> str:
    """업로드 파일을 저장하고 상대 경로를 반환한다.

    Args:
        file: FastAPI UploadFile 객체.
        upload_dir: 저장 디렉토리 경로 (예: "static/uploads").

    Returns:
        저장된 파일의 상대 경로 (예: "static/uploads/abc123.jpg").

    Raises:
        ValueError: 허용되지 않는 확장자일 때.
        IOError: 파일 저장 실패 시.
    """
    try:
        # 확장자 검증
        filename = file.filename or ""
        ext = filename.rsplit(".", maxsplit=1)[-1].lower() if "." in filename else ""
        if ext not in ALLOWED_EXTENSIONS:
            raise ValueError(
                f"허용되지 않는 확장자: .{ext} "
                f"(허용: {', '.join(sorted(ALLOWED_EXTENSIONS))})"
            )

        # UUID 기반 파일명 생성
        new_filename = f"{uuid.uuid4().hex}.{ext}"
        upload_path = Path(upload_dir)
        upload_path.mkdir(parents=True, exist_ok=True)

        file_path = upload_path / new_filename

        # 파일 저장
        content = await file.read()
        file_path.write_bytes(content)

        return str(file_path)

    except ValueError:
        raise
    except Exception as e:
        raise IOError(f"파일 저장 실패: {e}") from e


async def validate_file_size(file: UploadFile, max_size_mb: int) -> bool:
    """파일 크기가 제한 이내인지 검증한다.

    Args:
        file: FastAPI UploadFile 객체.
        max_size_mb: 최대 허용 크기 (MB).

    Returns:
        True이면 허용 범위 내, False이면 초과.
    """
    try:
        content = await file.read()
        size_mb = len(content) / (1024 * 1024)
        # 읽은 후 포인터를 되감아 다른 곳에서 다시 읽을 수 있도록
        await file.seek(0)
        return size_mb <= max_size_mb
    except Exception:
        return False
