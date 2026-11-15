from camcat.models import JobKind
from camcat.worker import job_kinds_for_role


def test_worker_roles_partition_every_job_kind_and_keep_legacy_all_mode() -> None:
    control = job_kinds_for_role("control")
    render = job_kinds_for_role("render")
    assert control is not None and render is not None
    assert control.isdisjoint(render)
    assert control | render == frozenset(JobKind)
    assert render == {JobKind.RENDER}
    assert job_kinds_for_role("all") is None


def test_analysis_cannot_block_localization_by_claiming_its_queue() -> None:
    analysis = job_kinds_for_role("analysis")
    localization = job_kinds_for_role("localization")
    render = job_kinds_for_role("render")
    assert analysis == {JobKind.INGEST_MEDIA, JobKind.ANALYZE_SOURCE}
    assert localization == {JobKind.TRANSLATE_SUBTITLES, JobKind.DUB_SUBTITLES}
    assert render is not None
    assert analysis.isdisjoint(localization | render)
    assert localization.isdisjoint(render)
    assert analysis | localization | render == frozenset(JobKind)
