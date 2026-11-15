"""Pure timing checks distinguish prompt prefill from incremental decoding."""

from services.local_models.generation import GenerationTiming


def test_prompt_delivery_is_not_counted_as_the_first_generated_token():
    clock = iter([10.0, 12.0, 14.0])
    timing = GenerationTiming(clock=lambda: next(clock))
    timing.put(object())
    timing.put(object())
    timing.put(object())
    timing.end()
    assert timing.metrics() == {"first_token_seconds": 2.0, "decode_seconds": 2.0}
