from __future__ import annotations

import math
from pathlib import Path

import pytest
from pydantic import ValidationError

from services.local_models.protocol import MediaObject, RerankRequest, normalize_mrl


def test_inline_image_is_materialized_without_changing_bytes_or_other_modalities(
    tmp_path: Path,
) -> None:
    import base64

    original = b"\x89PNG\r\n\x1a\noriginal-image-bytes"
    media = MediaObject(
        text="flowers",
        image_base64="data:image/png;base64," + base64.b64encode(original).decode(),
        metadata={"license_name": "CC0"},
    )
    item = media.materialize(tmp_path, "query")
    assert Path(item["image"]).read_bytes() == original
    assert item["text"] == "flowers"
    assert not item["image"].startswith("data:")
    assert media.metadata == {"license_name": "CC0"}


def test_corrupt_inline_image_encoding_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        MediaObject(image_base64="data:image/png;base64,$$$").materialize(tmp_path, "query")


def test_mrl_truncates_the_real_4096_output_then_normalizes_to_2048() -> None:
    original = [1.0] * 2048 + [100.0] * 2048
    output = normalize_mrl(original)
    assert len(output) == 2048
    assert math.fsum(value * value for value in output) == pytest.approx(1)
    assert output[0] == pytest.approx(1 / math.sqrt(2048))
    assert original[2048] == 100


@pytest.mark.parametrize(
    "values", [[1.0] * 2048, [0.0] * 4096, [float("nan")] * 4096, [float("inf")] * 4096]
)
def test_invalid_canonical_model_outputs_are_rejected(values: list[float]) -> None:
    with pytest.raises(ValueError):
        normalize_mrl(values)


def test_mixed_query_preserves_text_image_and_metadata_sidecar() -> None:
    media = MediaObject(
        text="the coast",
        image_base64="data:image/png;base64,AA==",
        metadata={"license_name": "CC0", "tags": ["coast"]},
    )
    official = media.official()
    assert official["text"] == "the coast"
    assert official["image"] == media.image_base64
    assert "metadata" not in official
    assert media.metadata["license_name"] == "CC0"


def test_empty_media_cannot_be_silently_replaced_by_text() -> None:
    with pytest.raises(ValidationError):
        MediaObject(text=" ")


def test_reranker_is_bounded_and_uses_the_canonical_model() -> None:
    request = RerankRequest(query={"text": "coast"}, documents=[{"text": "beach"}])
    assert request.model == "Qwen/Qwen3-VL-Reranker-8B"
    with pytest.raises(ValidationError):
        RerankRequest(query={"text": "coast"}, documents=[{"text": "beach"}] * 65)
    with pytest.raises(ValidationError):
        RerankRequest(
            model="a-different-model", query={"text": "coast"}, documents=[{"text": "beach"}]
        )
