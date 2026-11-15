from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.external]


@pytest.mark.skipif(
    not os.environ.get("CAMCAT_TRANSLATION_BASE_URL"), reason="real translation provider required"
)
def test_real_translation_preserves_ids_timing_names_and_numbers(tmp_path: Path) -> None:
    from camcat.config import get_settings
    from camcat.domain.localization import LocalizationCue, render_srt
    from camcat.localization.service import TranslationService

    settings = get_settings()
    cues = [
        LocalizationCue(
            cue_id="welcome",
            start_us=0,
            duration_us=5_000_000,
            source_text="欢迎使用 CamCat。",
            text="欢迎使用 CamCat。",
        ),
        LocalizationCue(
            cue_id="steps",
            start_us=5_000_000,
            duration_us=8_000_000,
            source_text="用 3 个步骤分享你的故事。",
            text="用 3 个步骤分享你的故事。",
        ),
    ]
    result = TranslationService(settings).translate(
        cues, language="en", glossary={"CamCat": "CamCat"}
    )
    assert [(c.cue_id, c.start_us, c.duration_us, c.source_text) for c in result] == [
        (c.cue_id, c.start_us, c.duration_us, c.source_text) for c in cues
    ]
    assert "CamCat" in result[0].text
    assert "3" in result[1].text or "three" in result[1].text.lower()
    (tmp_path / "translation.json").write_text(
        json.dumps(
            {
                "model": settings.translation_model,
                "cues": [c.model_dump() for c in result],
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    (tmp_path / "translation.srt").write_text(render_srt(result))
