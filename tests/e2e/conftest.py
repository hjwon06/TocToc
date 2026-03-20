"""E2E 테스트 conftest — 풀 플로우 + HTMX 검증.

QA 에이전트 소유. 통합 conftest의 mock을 재사용.
"""

from tests.integration.conftest import (  # noqa: F401
    mock_ocr,
    mock_ocr_failure,
    mock_save_upload,
    mock_thumbnail,
    mock_validate_file_size,
)
