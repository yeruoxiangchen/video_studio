"""Pure output boundary checks; actual structured reasoning uses the real compiler."""

import pytest

from services.local_models.generation import final_json_content, reasoning_structure


def test_private_reasoning_is_removed_before_the_api_json_response():
    assert (
        final_json_content('Private notes\n</think>\n\n{"clips":[]}', reasoning=True)
        == '{"clips":[]}'
    )


def test_a_missing_reasoning_boundary_cannot_be_published_as_an_answer():
    with pytest.raises(ValueError, match="reasoning boundary"):
        final_json_content('{"clips":[]}', reasoning=True)


def test_plain_json_keeps_its_existing_output_contract():
    assert final_json_content('{"ok":true}', reasoning=False) == '{"ok":true}'


def test_reasoning_budget_keeps_the_original_answer_schema():
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
    structure = reasoning_structure(schema)
    elements = structure["format"]["elements"]
    assert elements[0]["content"]["max_tokens"] == 1536
    assert elements[-1]["json_schema"] == schema
