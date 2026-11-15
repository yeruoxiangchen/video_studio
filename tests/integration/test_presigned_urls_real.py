from __future__ import annotations

import io
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import pytest
from camcat.config import get_settings
from camcat.services.object_store import ObjectStore

pytestmark = pytest.mark.integration


def test_cached_real_signatures_preserve_download_options_expiry_and_bytes() -> None:
    store = ObjectStore(get_settings())
    key = f"temporary/diagnostics/signature-cache/{uuid4()}.txt"
    content = b"CamCat real S3 signature verification."
    store.upload_stream(io.BytesIO(content), key, "text/plain")
    try:
        playback = store.signed_url(key, expires_seconds=60)
        assert store.signed_url(key, expires_seconds=60) == playback
        assert parse_qs(urlsplit(playback).query)["X-Amz-Expires"] == ["60"]
        longer = store.signed_url(key, expires_seconds=3600)
        assert longer != playback
        assert parse_qs(urlsplit(longer).query)["X-Amz-Expires"] == ["3600"]
        with httpx.Client(trust_env=False, timeout=30) as client:
            response = client.get(playback)
            assert response.status_code == 200
            assert response.content == content
            for filename in ("one.txt", "two.txt", ""):
                download = store.signed_download_url(key, filename, expires_seconds=60)
                assert download != playback
                response = client.get(download)
                assert response.status_code == 200
                assert response.content == content
                assert (
                    response.headers["content-disposition"] == f'attachment; filename="{filename}"'
                )
    finally:
        store.delete_key(key)
