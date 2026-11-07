"""orders workflow, stock reservation, agent assignments, legal requisites

* order status `confirmed` -> `approved` (data migrated, column renamed)
* orders: submitted/approved/shipped timestamps, idempotency key, creator, cancel reason
* products.reserved_quantity (stock held by approved orders)
* agent_store_assignments
* requisites for official documents on wholesalers and stores

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_STATUSES = "'draft', 'pending', 'approved', 'shipped', 'delivered', 'cancelled'"
OLD_STATUSES = "'draft', 'pending', 'confirmed', 'shipped', 'delivered', 'cancelled'"

REQUISITE_COLUMNS = (
    ("legal_name", sa.String(length=300)),
    ("tin", sa.String(length=20)),
    ("bank_name", sa.String(length=200)),
    ("bank_account", sa.String(length=34)),
    ("bank_mfo", sa.String(length=10)),
)


def upgrade() -> None:
    # --- agent <-> store assignments -------------------------------------------------
    op.create_table(
        "agent_store_assignments",
        sa.Column("wholesaler_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("store_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], name=op.f("fk_agent_store_assignments_agent_id_agents"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"], name=op.f("fk_agent_store_assignments_store_id_stores"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["wholesaler_id"], ["wholesalers.id"], name=op.f("fk_agent_store_assignments_wholesaler_id_wholesalers"), ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_store_assignments")),
        sa.UniqueConstraint("agent_id", "store_id", name=op.f("uq_agent_store_assignments_agent_id_store_id")),
    )
    op.create_index(op.f("ix_agent_store_assignments_store_id"), "agent_store_assignments", ["store_id"])
    op.create_index(op.f("ix_agent_store_assignments_wholesaler_id"), "agent_store_assignments", ["wholesaler_id"])

    # --- orders: confirmed -> approved ---------------------------------------------------
    op.drop_constraint(op.f("ck_orders_order_status"), "orders", type_="check")
    op.execute("UPDATE orders SET status = 'approved' WHERE status = 'confirmed'")
    op.create_check_constraint(op.f("ck_orders_order_status"), "orders", f"status IN ({NEW_STATUSES})")
    op.alter_column("orders", "confirmed_at", new_column_name="approved_at")

    op.add_column("orders", sa.Column("cancel_reason", sa.Text(), nullable=True))
    op.add_column("orders", sa.Column("created_by_telegram_user_id", sa.BigInteger(), nullable=True))
    op.add_column("orders", sa.Column("idempotency_key", sa.String(length=128), nullable=True))
    op.add_column("orders", sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("orders", sa.Column("shipped_at", sa.DateTime(timezone=True), nullable=True))
    op.create_unique_constraint(
        op.f("uq_orders_wholesaler_id_idempotency_key"), "orders", ["wholesaler_id", "idempotency_key"]
    )
    # Backfill timestamps for existing rows so the new invariants hold.
    op.execute(
        "UPDATE orders SET submitted_at = created_at "
        "WHERE submitted_at IS NULL AND status NOT IN ('draft', 'cancelled')"
    )
    op.execute(
        "UPDATE orders SET approved_at = COALESCE(approved_at, updated_at) "
        "WHERE approved_at IS NULL AND status IN ('approved', 'shipped', 'delivered')"
    )
    op.create_check_constraint(
        op.f("ck_orders_submitted_at_matches"),
        "orders",
        "status IN ('draft', 'cancelled') OR submitted_at IS NOT NULL",
    )
    op.create_check_constraint(
        op.f("ck_orders_approved_at_matches"),
        "orders",
        "status NOT IN ('approved', 'shipped', 'delivered') OR approved_at IS NOT NULL",
    )

    # --- products: reservation -------------------------------------------------------------
    op.add_column(
        "products",
        sa.Column("reserved_quantity", sa.Numeric(precision=15, scale=3), server_default="0", nullable=False),
    )
    op.create_check_constraint(
        op.f("ck_products_reserved_within_stock"),
        "products",
        "reserved_quantity >= 0 AND reserved_quantity <= stock_quantity",
    )

    # --- requisites ------------------------------------------------------------------------
    op.add_column("wholesalers", sa.Column("address", sa.Text(), nullable=True))
    for table in ("wholesalers", "stores"):
        for name, type_ in REQUISITE_COLUMNS:
            op.add_column(table, sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    for table in ("wholesalers", "stores"):
        for name, _ in REQUISITE_COLUMNS:
            op.drop_column(table, name)
    op.drop_column("wholesalers", "address")

    op.drop_constraint(op.f("ck_products_reserved_within_stock"), "products", type_="check")
    op.drop_column("products", "reserved_quantity")

    op.drop_constraint(op.f("ck_orders_approved_at_matches"), "orders", type_="check")
    op.drop_constraint(op.f("ck_orders_submitted_at_matches"), "orders", type_="check")
    op.drop_constraint(op.f("uq_orders_wholesaler_id_idempotency_key"), "orders", type_="unique")
    for column in ("shipped_at", "submitted_at", "idempotency_key", "created_by_telegram_user_id", "cancel_reason"):
        op.drop_column("orders", column)
    op.alter_column("orders", "approved_at", new_column_name="confirmed_at")
    op.drop_constraint(op.f("ck_orders_order_status"), "orders", type_="check")
    op.execute("UPDATE orders SET status = 'confirmed' WHERE status = 'approved'")
    op.create_check_constraint(op.f("ck_orders_order_status"), "orders", f"status IN ({OLD_STATUSES})")

    op.drop_index(op.f("ix_agent_store_assignments_wholesaler_id"), table_name="agent_store_assignments")
    op.drop_index(op.f("ix_agent_store_assignments_store_id"), table_name="agent_store_assignments")
    op.drop_table("agent_store_assignments")
