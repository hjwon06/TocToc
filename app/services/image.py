"""이미지 서비스 — 썸네일 생성, 이미지 삭제, URL 변환.

A4 비즈니스 에이전트 소유 파일.
"""

import logging
from pathlib import Path

from PIL import Image

logger = logging.getLogger(__name__)

# 썸네일 설정
THUMBNAIL_SIZE = (300, 300)
THUMBNAIL_SUFFIX = "_thumb"
THUMBNAIL_FORMAT = "JPEG"
THUMBNAIL_QUALITY = 80

# 이미지 압축 설정
COMPRESS_MAX_LONG_SIDE = 1920
COMPRESS_QUALITY = 85


def image_path_to_url(image_path: str) -> str:
    """저장된 이미지 경로를 URL 경로로 변환한다.

    예: "static/uploads/abc123.jpg" → "/static/uploads/abc123.jpg"
    """
    # 이미 /로 시작하면 그대로 반환
    if image_path.startswith("/"):
        return image_path
    return f"/{image_path}"


def get_thumbnail_path(image_path: str) -> str:
    """이미지 경로에서 썸네일 경로를 생성한다.

    예: "static/uploads/abc123.jpg" → "static/uploads/abc123_thumb.jpg"
    """
    path = Path(image_path)
    return str(path.with_stem(f"{path.stem}{THUMBNAIL_SUFFIX}"))


def create_thumbnail(image_path: str) -> str | None:
    """이미지 파일의 썸네일을 생성한다.

    Args:
        image_path: 원본 이미지 경로.

    Returns:
        썸네일 경로 또는 실패 시 None.
    """
    try:
        path = Path(image_path)
        if not path.exists():
            logger.warning("썸네일 생성 실패 — 원본 파일 없음: %s", image_path)
            return None

        thumb_path = get_thumbnail_path(image_path)

        with Image.open(path) as img:
            img.thumbnail(THUMBNAIL_SIZE)
            # RGBA → RGB 변환 (JPEG 저장 위해)
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            img.save(thumb_path, format=THUMBNAIL_FORMAT, quality=THUMBNAIL_QUALITY)

        logger.info("썸네일 생성 완료: %s", thumb_path)
        return thumb_path

    except Exception as e:
        logger.error("썸네일 생성 실패: %s — %s", image_path, e)
        return None


def compress_image(image_path: str) -> bool:
    """업로드된 이미지를 압축 + 리사이즈한다.

    긴 변 최대 1920px, JPEG quality 85.
    이미 작은 이미지는 건드리지 않는다.

    Returns:
        True이면 압축 완료, False이면 실패 또는 스킵.
    """
    try:
        path = Path(image_path)
        if not path.exists():
            logger.warning("압축 실패 — 파일 없음: %s", image_path)
            return False

        with Image.open(path) as img:
            w, h = img.size
            long_side = max(w, h)

            # 이미 작으면 품질만 재압축
            if long_side <= COMPRESS_MAX_LONG_SIDE:
                if img.mode in ("RGBA", "P"):
                    img = img.convert("RGB")
                img.save(path, format="JPEG", quality=COMPRESS_QUALITY)
                return True

            # 비율 유지 리사이즈
            ratio = COMPRESS_MAX_LONG_SIDE / long_side
            new_size = (int(w * ratio), int(h * ratio))
            resized = img.resize(new_size, Image.Resampling.LANCZOS)

            if resized.mode in ("RGBA", "P"):
                resized = resized.convert("RGB")
            resized.save(path, format="JPEG", quality=COMPRESS_QUALITY)

        logger.info("이미지 압축 완료: %s (%dx%d → %dx%d)", image_path, w, h, *new_size)
        return True

    except Exception as e:
        logger.error("이미지 압축 실패: %s — %s", image_path, e)
        return False


def delete_image(image_path: str) -> bool:
    """이미지 파일과 썸네일을 삭제한다.

    Args:
        image_path: 삭제할 이미지 경로.

    Returns:
        True이면 삭제 성공, False이면 실패.
    """
    try:
        path = Path(image_path)
        deleted = False

        # 원본 삭제
        if path.exists():
            path.unlink()
            deleted = True
            logger.info("이미지 삭제: %s", image_path)

        # 썸네일 삭제
        thumb_path = Path(get_thumbnail_path(image_path))
        if thumb_path.exists():
            thumb_path.unlink()
            logger.info("썸네일 삭제: %s", thumb_path)

        return deleted

    except Exception as e:
        logger.error("이미지 삭제 실패: %s — %s", image_path, e)
        return False


def get_image_url(image_path: str | None) -> str:
    """이미지 경로를 URL로 변환, None이면 플레이스홀더 반환."""
    if not image_path:
        return "/static/placeholder.svg"
    return image_path_to_url(image_path)


def get_thumbnail_url(image_path: str | None) -> str:
    """썸네일 URL 반환, 썸네일이 없으면 원본 URL 반환."""
    if not image_path:
        return "/static/placeholder.svg"

    thumb_path = get_thumbnail_path(image_path)
    if Path(thumb_path).exists():
        return image_path_to_url(thumb_path)

    return image_path_to_url(image_path)
