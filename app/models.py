"""SQLAlchemy 2.0 ORM 모델 — DeclarativeBase 사용."""

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Index, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """모든 모델의 베이스 클래스."""


class Receipt(Base):
    """영수증 테이블."""

    __tablename__ = "receipts"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    image_path: Mapped[str] = mapped_column(
        String(500), nullable=False, comment="저장된 이미지 상대 경로"
    )
    receipt_date: Mapped[date | None] = mapped_column(
        Date, nullable=True, comment="영수증 날짜"
    )
    amount: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="금액(원 단위)"
    )
    is_manual: Mapped[bool] = mapped_column(
        Boolean, default=False, comment="수동 입력 여부"
    )
    ocr_raw: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="OCR 원본 텍스트"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        comment="생성 시각",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        comment="수정 시각",
    )

    __table_args__ = (
        Index("idx_receipts_date", "receipt_date"),
        Index("idx_receipts_created", "created_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<Receipt(id={self.id}, date={self.receipt_date}, "
            f"amount={self.amount})>"
        )
