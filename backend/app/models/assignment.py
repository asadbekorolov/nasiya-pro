from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin


class AgentStoreAssignment(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Which stores an agent serves. An agent may only place orders, deliver and
    collect cash for stores assigned to them."""

    __tablename__ = "agent_store_assignments"

    wholesaler_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("wholesalers.id", ondelete="RESTRICT"), index=True
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"))
    store_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("stores.id", ondelete="CASCADE"), index=True
    )

    __table_args__ = (UniqueConstraint("agent_id", "store_id"),)
