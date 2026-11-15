"""Pure CLI targeting checks; external behavior is tested against real services."""

from pathlib import Path

from scripts.run_local_integration import integration_arguments, local_provider_url


def test_explicit_integration_files_replace_the_default_suite():
    args = integration_arguments(Path("/data/example"), ["tests/integration/test_one.py"])
    assert "tests/integration/test_one.py" in args
    assert "tests/integration" not in args


def test_default_integration_invocation_retains_the_whole_suite():
    assert "tests/integration" in integration_arguments(Path("/data/example"), [])


def test_host_integration_preserves_the_selected_visual_model_port():
    assert local_provider_url("http://10.0.2.2:18972") == "http://127.0.0.1:18972"
    assert local_provider_url("http://10.0.2.2:18969") == "http://127.0.0.1:18969"
    assert local_provider_url("http://127.0.0.1:9000") == "http://127.0.0.1:9000"
