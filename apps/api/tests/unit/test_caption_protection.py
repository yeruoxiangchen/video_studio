import pytest
from camcat.agent.caption_repair import retains_caption_terms, validate_caption_terms


def test_caption_names_survive_shortening_as_whole_names():
    assert retains_caption_terms("Hi Camcat!", ["Camcat"])
    assert not retains_caption_terms("Welcome!", ["Camcat"])
    assert not retains_caption_terms("CamcatX", ["Camcat"])
    assert retains_caption_terms("剪映创作", ["剪映"])
    assert not retains_caption_terms("40 shots", ["4"])


def test_extracted_protection_terms_are_bound_to_the_actual_original_caption():
    assert validate_caption_terms("Welcome to Camcat!", ["Camcat", "Camcat"]) == ["Camcat"]
    with pytest.raises(ValueError, match="original caption"):
        validate_caption_terms("Welcome to Camcat!", ["Another Brand"])
