"""lock down hosted data APIs (Supabase): row level security, no anon/authenticated grants

Supabase publishes every table of the `public` schema through its REST/GraphQL Data
API, authorised by the project's anon key (public by design). Nasiya Pro never uses
that API: all access goes through the FastAPI backend, which connects as the table
owner. So:

* RLS is enabled on every table with NO policies -> the Data API roles see nothing.
  The owner role bypasses RLS, so the backend is unaffected.
* Table/sequence/function privileges of `anon` and `authenticated` are revoked, now and
  for objects created later (default privileges).

On plain PostgreSQL (local, tests, Docker) those roles do not exist and only the
(harmless) RLS switch is applied.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-08
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = (
    "wholesalers",
    "stores",
    "agents",
    "agent_store_assignments",
    "products",
    "orders",
    "order_items",
    "debts",
    "debt_allocations",
    "transactions",
    "payment_otp_verifications",
    "alembic_version",
)

API_ROLES = ("anon", "authenticated")


def _for_existing_api_roles(sql_template: str) -> str:
    """Run `sql_template` (with {role}) only for Data API roles that exist.
    Built only from the constants above, never from input."""
    checks = "\n".join(
        f"""
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
            {sql_template.format(role=role)}
        END IF;"""  # noqa: S608 - constant role names, no user input
        for role in API_ROLES
    )
    return f"DO $$\nBEGIN{checks}\nEND\n$$;"


def upgrade() -> None:
    for table in TABLES:
        op.execute(f'ALTER TABLE public."{table}" ENABLE ROW LEVEL SECURITY')

    op.execute(
        _for_existing_api_roles(
            """
            EXECUTE 'REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {role}';
            EXECUTE 'REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {role}';
            EXECUTE 'REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM {role}';
            EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM {role}';
            EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM {role}';
            EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON FUNCTIONS FROM {role}';"""
        )
    )


def downgrade() -> None:
    # Grants are deliberately not restored: re-exposing the ledger is never wanted.
    for table in TABLES:
        op.execute(f'ALTER TABLE public."{table}" DISABLE ROW LEVEL SECURITY')
