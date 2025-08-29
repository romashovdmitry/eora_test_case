# Python imports
import uuid
from datetime import datetime

# SQLAlchemy imports
from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

# Local imports
from main.base_model import Base


class LogEntry(Base):
    __tablename__ = "logs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4, index=True)
    level: Mapped[str] = mapped_column(String, nullable=False)
    message: Mapped[str] = mapped_column(String, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
