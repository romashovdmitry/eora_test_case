from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, event
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from main.base_model import Base


class SourceContent(Base):
    """
    Модель для сохранения контента, спарсенного с URL-ов из SourceURL
    """
    __tablename__ = "source_content"
    
    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
        autoincrement=True,
        comment="Primary key of the source content"
    )
    source_url_id: Mapped[int] = mapped_column(
        ForeignKey("source_urls.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Foreign key to source_urls table"
    )
    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Parsed text content from the URL"
    )
    title: Mapped[Optional[str]] = mapped_column(
        String(500),
        nullable=True,
        comment="Title extracted from the URL content"
    )
    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Description or summary extracted from the URL"
    )
    extraction_metadata: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSON,
        nullable=True,
        comment="Metadata about the extraction process (e.g., method, settings, stats)"
    )
    parsed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="When this content was parsed and saved"
    )
    
    # Связь с таблицей source_urls
    source_url: Mapped["SourceURL"] = relationship(
        "SourceURL",
        back_populates="contents"
    )
    
    def __str__(self) -> str:
        """Читаемое представление для админ панели"""
        content_preview = self.content[:50] + "..." if len(self.content) > 50 else self.content
        return f"Контент для: {self.source_url.title if self.source_url else 'Unknown'} ({content_preview})"

    def __repr__(self) -> str:
        return f"<SourceContent(id={self.id}, source_url_id={self.source_url_id}, content_length={len(self.content)})>"
