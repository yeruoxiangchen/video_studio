"""CPU-only grammar contract check using the real deployed tokenizer and command schema."""

from __future__ import annotations

import json
import sys
import time

import xgrammar as xgr
from transformers import AutoTokenizer

from services.local_models.generation import StructuredDecoder


def main() -> None:
    packet = json.load(sys.stdin)
    tokenizer = AutoTokenizer.from_pretrained(packet["tokenizer"], local_files_only=True)
    decoder = StructuredDecoder(tokenizer, packet["vocab_size"])
    started = time.monotonic()
    compiled = decoder.compile(packet["schema"])
    compiled_seconds = time.monotonic() - started
    valid = json.dumps(packet["valid"], ensure_ascii=False)
    matcher = xgr.GrammarMatcher(compiled, terminate_without_stop_token=True)
    assert matcher.accept_string(valid), "Native valid command was rejected"
    invalid = json.dumps(packet["invalid"], ensure_ascii=False)
    assert not xgr.GrammarMatcher(compiled).accept_string(invalid), "Invalid object was accepted"
    for invalid_text in packet.get("invalid_json", []):
        assert not xgr.GrammarMatcher(compiled).accept_string(invalid_text), (
            "Invalid JSON was accepted"
        )
    tokens = tokenizer.encode(valid, add_special_tokens=False)
    matcher = xgr.GrammarMatcher(compiled)
    mask = xgr.allocate_token_bitmask(1, packet["vocab_size"])
    started = time.monotonic()
    for token in tokens:
        matcher.fill_next_token_bitmask(mask)
        assert matcher.accept_token(token), "Actual tokenizer disagrees with the grammar"
    mask_seconds = time.monotonic() - started
    assert mask_seconds < 10, "Grammar mask exceeds the local decoding budget"
    print(
        json.dumps(
            {
                "kind": "grammar_contract_without_model_inference",
                "backend": "xgrammar",
                "schema_bytes": len(json.dumps(packet["schema"])),
                "tokens": len(tokens),
                "compile_seconds": compiled_seconds,
                "mask_seconds": mask_seconds,
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
