"""Pure generation configuration regression for chat-vs-base EOS mismatch."""

from types import SimpleNamespace

from services.local_models.generation import chat_generation_options


def test_chat_stops_at_the_same_token_used_by_the_structured_decoder():
    tokenizer = SimpleNamespace(eos_token_id=248046, pad_token_id=248044)
    options = chat_generation_options(tokenizer, 0)
    assert options["eos_token_id"] == 248046
    assert options["pad_token_id"] == 248044
    assert options["do_sample"] is False


def test_an_unset_pad_id_uses_the_chat_stop_id():
    options = chat_generation_options(SimpleNamespace(eos_token_id=42, pad_token_id=None), 0.1)
    assert options["pad_token_id"] == options["eos_token_id"] == 42
    assert options["temperature"] == 0.1
