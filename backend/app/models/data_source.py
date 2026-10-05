from sqlalchemy import CheckConstraint, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin
from app.models.enums import DataSourceType, data_source_type_enum


class DataSource(TimestampMixin, Base):
    __tablename__ = "data_sources"
    __table_args__ = (
        UniqueConstraint("code", name="uq_data_sources_code"),
        CheckConstraint(
            "freshness_seconds IS NULL OR freshness_seconds > 0",
            name="ck_data_sources_freshness_seconds_positive",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200))
    source_type: Mapped[DataSourceType] = mapped_column(data_source_type_enum)
    url: Mapped[str | None] = mapped_column(Text)
    license: Mapped[str | None] = mapped_column(Text)
    attribution: Mapped[str | None] = mapped_column(Text)
    # Realtime freshness threshold for this source; NULL means use service default.
    freshness_seconds: Mapped[int | None] = mapped_column(Integer)
