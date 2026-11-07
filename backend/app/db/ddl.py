"""Raw DDL that SQLAlchemy metadata cannot express: append-only enforcement for the
ledger tables. Shared by the ORM models (for `metadata.create_all` in tests) and by
the Alembic migrations, so both paths produce the identical schema."""

from __future__ import annotations

APPEND_ONLY_TABLES: tuple[str, ...] = ("transactions", "debt_allocations")

CREATE_APPEND_ONLY_FUNCTION = """
CREATE OR REPLACE FUNCTION nasiya_forbid_ledger_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'table "%" is append-only: % is not allowed', TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'restrict_violation',
              HINT = 'Post a compensating "adjustment" transaction instead.';
END;
$$;
"""

DROP_APPEND_ONLY_FUNCTION = "DROP FUNCTION IF EXISTS nasiya_forbid_ledger_mutation();"


def create_append_only_triggers(table: str) -> list[str]:
    return [
        f"""
        CREATE TRIGGER trg_{table}_append_only_row
        BEFORE UPDATE OR DELETE ON {table}
        FOR EACH ROW EXECUTE FUNCTION nasiya_forbid_ledger_mutation();
        """,
        f"""
        CREATE TRIGGER trg_{table}_append_only_truncate
        BEFORE TRUNCATE ON {table}
        FOR EACH STATEMENT EXECUTE FUNCTION nasiya_forbid_ledger_mutation();
        """,
    ]


def drop_append_only_triggers(table: str) -> list[str]:
    return [
        f"DROP TRIGGER IF EXISTS trg_{table}_append_only_row ON {table};",
        f"DROP TRIGGER IF EXISTS trg_{table}_append_only_truncate ON {table};",
    ]
