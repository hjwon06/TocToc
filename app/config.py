"""애플리케이션 설정 — pydantic-settings 기반."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """환경변수(.env)에서 로드되는 앱 설정."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Database
    DATABASE_URL: str = (
        "postgresql+asyncpg://postgres:password@localhost:5432/toctoc"
    )

    # Claude API (Vision OCR)
    ANTHROPIC_API_KEY: str = ""

    # File Upload
    UPLOAD_DIR: str = "static/uploads"
    MAX_FILE_SIZE_MB: int = 10

    # App
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000


settings = Settings()
