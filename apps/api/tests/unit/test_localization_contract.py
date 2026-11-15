"""Offline API/schema and retention checks, not live-service acceptance."""

import json

from camcat.api import app
from camcat.models import JobKind
from camcat.repositories import redact_transient_document
from test_localization import source_project


def test_localization_extends_the_original_persistent_job_types():
    assert JobKind.INGEST_MEDIA.value == "ingest_media"
    assert JobKind.ANALYZE_SOURCE.value == "analyze_source"
    assert JobKind.RENDER.value == "render"
    assert JobKind.TRANSLATE_SUBTITLES.value == "translate_subtitles"
    assert JobKind.DUB_SUBTITLES.value == "dub_subtitles"


def test_api_exposes_localization_jobs_and_explicit_versioned_application():
    paths = app.openapi()["paths"]
    prefix = "/api/v1/editing/sessions/{session_id}/localization"
    assert "get" in paths[prefix]
    for name in ("translate", "dub"):
        assert "202" in paths[f"{prefix}/{name}"]["post"]["responses"]
    apply = paths[f"{prefix}/{{job_id}}/apply"]["post"]
    assert apply["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/ApplyLocalizationRequest"
    )


def test_expired_user_video_also_redacts_derived_subtitles_and_lineage():
    document = source_project().model_dump(mode="json", by_alias=True)
    document["sources"][0].update(
        origin="user_upload", retention_class="transient_4h", storage_key="temporary/source.mp4"
    )
    document["metadata"]["localization"] = {"language": "en", "source_text_by_id": {"s1": "你好。"}}
    redacted = redact_transient_document(document)
    assert "你好" not in json.dumps(redacted, ensure_ascii=False)
    assert redacted["metadata"]["localization"] == {"expired": True}
