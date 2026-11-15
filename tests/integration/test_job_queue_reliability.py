from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from uuid import uuid4

import pytest
from camcat.config import get_settings
from camcat.database import Base
from camcat.models import Job, JobKind, JobStatus, utcnow
from camcat.repositories import JobRepository
from camcat.worker import job_kinds_for_role
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def queue_sessions() -> Iterator[sessionmaker[Session]]:
    """Real Postgres, isolated from the continuously polling application worker."""
    url = make_url(get_settings().database_url)
    database_name = "camcat_queue_" + uuid4().hex
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    isolated = create_engine(url.set(database=database_name), pool_pre_ping=True)
    try:
        Base.metadata.create_all(isolated)
        yield sessionmaker(bind=isolated, expire_on_commit=False)
    finally:
        isolated.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{database_name}" WITH (FORCE)'))
        admin.dispose()


def test_job_idempotency_expired_lease_retry_and_dead_letter(
    queue_sessions: sessionmaker[Session],
) -> None:
    owner = f"queue-{uuid4()}"
    key = f"ingest-{uuid4()}"
    with queue_sessions() as db:
        repository = JobRepository(db)
        first = repository.enqueue(
            owner_id=owner,
            kind=JobKind.INGEST_MEDIA,
            payload={"asset_id": str(uuid4())},
            idempotency_key=key,
            max_attempts=2,
        )
        duplicate = repository.enqueue(
            owner_id=owner,
            kind=JobKind.INGEST_MEDIA,
            payload={"asset_id": str(uuid4())},
            idempotency_key=key,
            max_attempts=2,
        )
        assert duplicate.id == first.id
        first.available_at = utcnow() - timedelta(days=365)
        db.commit()
        claimed = repository.claim_next(worker_id="worker-a", lease_seconds=60)
        assert claimed is not None
        assert claimed.id == first.id
        assert claimed.attempts == 1
        claimed.lease_expires_at = utcnow() - timedelta(seconds=1)
        db.commit()

    with queue_sessions() as db:
        repository = JobRepository(db)
        reclaimed = repository.claim_next(worker_id="worker-b", lease_seconds=60)
        assert reclaimed is not None
        assert reclaimed.id == first.id
        assert reclaimed.attempts == 2
        repository.fail(reclaimed, "provider unavailable")
        assert reclaimed.status == JobStatus.DEAD_LETTER
        db.delete(reclaimed)
        db.commit()


def test_queued_job_can_be_cancelled_and_explicitly_retried(
    queue_sessions: sessionmaker[Session],
) -> None:
    with queue_sessions() as db:
        repository = JobRepository(db)
        job = repository.enqueue(owner_id=f"cancel-{uuid4()}", kind=JobKind.RENDER, payload={})
        repository.cancel(job)
        assert job.status == JobStatus.CANCELLED
        repository.retry(job)
        assert job.status == JobStatus.QUEUED
        db.delete(db.get(Job, job.id))
        db.commit()


def test_crashed_job_at_attempt_limit_is_persisted_for_compensation(
    queue_sessions: sessionmaker[Session],
) -> None:
    with queue_sessions() as db:
        repository = JobRepository(db)
        job = repository.enqueue(
            owner_id=f"crashed-{uuid4()}",
            kind=JobKind.INGEST_MEDIA,
            payload={"asset_id": str(uuid4())},
            max_attempts=1,
        )
        job.available_at = utcnow() - timedelta(days=365)
        db.commit()
        claimed = repository.claim_next(worker_id="worker-that-crashes", lease_seconds=60)
        assert claimed is not None and claimed.id == job.id
        claimed.lease_expires_at = utcnow() - timedelta(seconds=1)
        db.commit()

    with queue_sessions() as db:
        repository = JobRepository(db)
        expired = repository.expire_exhausted_leases()
        assert [item.id for item in expired] == [job.id]
        pending = repository.pending_ingest_compensations()
        assert job.id in {item.id for item in pending}
        persisted = db.get(Job, job.id)
        assert persisted is not None
        assert persisted.status == JobStatus.DEAD_LETTER
        assert persisted.checkpoint["stage"] == "dead_lettered"
        db.delete(persisted)
        db.commit()


def test_checkpoint_survives_heartbeat_refresh_and_fresh_database_read(
    queue_sessions: sessionmaker[Session],
) -> None:
    with queue_sessions() as db:
        repository = JobRepository(db)
        job = repository.enqueue(owner_id=f"checkpoint-{uuid4()}", kind=JobKind.RENDER, payload={})
        claimed = repository.claim_next(worker_id="checkpoint-worker")
        assert claimed is not None and claimed.id == job.id
        repository.checkpoint(claimed, "encoded", {"output_bytes": 1234})
        job_id = claimed.id
    with queue_sessions() as db:
        persisted = db.get(Job, job_id)
        assert persisted is not None
        assert persisted.checkpoint["stage"] == "encoded"
        assert persisted.checkpoint["data"] == {"output_bytes": 1234}
        assert persisted.heartbeat_at is not None
        assert persisted.lease_expires_at is not None
        db.delete(persisted)
        db.commit()


def test_visual_checkpoint_survives_retry_and_is_removed_at_input_expiry(
    queue_sessions: sessionmaker[Session],
) -> None:
    with queue_sessions() as db:
        repository = JobRepository(db)
        job = repository.enqueue(
            owner_id=f"retention-{uuid4()}",
            kind=JobKind.ANALYZE_SOURCE,
            payload={"purpose": "retention fixture"},
        )
        job.expires_at = utcnow() + timedelta(hours=1)
        job.checkpoint = {
            "stage": "source_visual_ready",
            "source_visual_cache": {"private-input": "retention sentinel"},
        }
        db.commit()
        identifier = job.id
        repository.fail(job, "temporary interruption", retryable=True)
    with queue_sessions() as db:
        repository = JobRepository(db)
        job = db.get(Job, identifier)
        assert job is not None
        assert job.checkpoint["source_visual_cache"] == {"private-input": "retention sentinel"}
        repository.redact_expired(now=utcnow() + timedelta(hours=2))
    with queue_sessions() as db:
        job = db.get(Job, identifier)
        assert job is not None
        assert "source_visual_cache" not in job.checkpoint
        assert job.checkpoint["stage"] == "source_visual_ready"
        assert job.payload["redacted"] is True
        db.delete(job)
        db.commit()


def test_workers_claim_only_their_assigned_kinds_without_blocking_other_lanes(
    queue_sessions: sessionmaker[Session],
) -> None:
    with queue_sessions() as db:
        repository = JobRepository(db)
        render = repository.enqueue(owner_id=f"lanes-{uuid4()}", kind=JobKind.RENDER, payload={})
        control = repository.enqueue(
            owner_id=render.owner_id, kind=JobKind.TRANSLATE_SUBTITLES, payload={}
        )
        render.available_at = utcnow() - timedelta(days=2)
        control.available_at = utcnow() - timedelta(days=1)
        db.commit()
        selected = repository.claim_next(
            worker_id="control-worker", kinds=frozenset(JobKind) - {JobKind.RENDER}
        )
        assert selected is not None and selected.id == control.id
        assert repository.claim_next(worker_id="control-worker", kinds=frozenset()) is None
        selected_render = repository.claim_next(
            worker_id="render-worker", kinds=frozenset({JobKind.RENDER})
        )
        assert selected_render is not None and selected_render.id == render.id
        assert selected.worker_id != selected_render.worker_id
        repository.succeed(selected, {})
        repository.succeed(selected_render, {})
        db.delete(selected)
        db.delete(selected_render)
        db.commit()


def test_localization_claims_immediately_while_analysis_has_an_active_lease(
    queue_sessions: sessionmaker[Session],
) -> None:
    with queue_sessions() as db:
        repository = JobRepository(db)
        analysis = repository.enqueue(
            owner_id=f"isolated-{uuid4()}", kind=JobKind.ANALYZE_SOURCE, payload={}
        )
        translation = repository.enqueue(
            owner_id=analysis.owner_id, kind=JobKind.TRANSLATE_SUBTITLES, payload={}
        )
        analysis.available_at = utcnow() - timedelta(days=2)
        translation.available_at = utcnow() - timedelta(days=1)
        db.commit()
        selected = repository.claim_next(
            worker_id="analysis-worker", kinds=job_kinds_for_role("analysis")
        )
        assert selected is not None and selected.id == analysis.id
        with queue_sessions() as independent:
            second = JobRepository(independent).claim_next(
                worker_id="localization-worker", kinds=job_kinds_for_role("localization")
            )
            assert second is not None and second.id == translation.id
            JobRepository(independent).succeed(second, {})
            independent.delete(second)
            independent.commit()
        db.refresh(selected)
        assert selected.status == JobStatus.RUNNING
        assert selected.worker_id == "analysis-worker"
        repository.succeed(selected, {})
        db.delete(selected)
        db.commit()
