"""Add localization jobs without replacing the CamCat editing/ingestion jobs."""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE jobkind ADD VALUE IF NOT EXISTS 'TRANSLATE_SUBTITLES'")
        op.execute("ALTER TYPE jobkind ADD VALUE IF NOT EXISTS 'DUB_SUBTITLES'")


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM jobs
            WHERE kind::text IN ('TRANSLATE_SUBTITLES','DUB_SUBTITLES')) THEN
            RAISE EXCEPTION 'Localization jobs exist; preserve history before downgrading';
          END IF;
        END $$
    """)
    op.execute("ALTER TABLE jobs ALTER COLUMN kind TYPE VARCHAR USING kind::text")
    op.execute("DROP TYPE jobkind")
    op.execute("CREATE TYPE jobkind AS ENUM ('INGEST_MEDIA','ANALYZE_SOURCE','RENDER')")
    op.execute("ALTER TABLE jobs ALTER COLUMN kind TYPE jobkind USING kind::jobkind")
