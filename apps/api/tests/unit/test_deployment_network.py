from scripts.capture_network import proxy_environments


def test_host_proxy_stays_on_loopback_and_container_proxy_uses_rootless_gateway() -> None:
    host, container = proxy_environments(
        {
            "HTTPS_PROXY": "http://user:secret@127.0.0.1:17891",
            "NO_PROXY": "localhost,.docker.io",
            "UNRELATED_SECRET": "do-not-export",
        }
    )
    assert host["HTTPS_PROXY"] == "http://user:secret@127.0.0.1:17891"
    assert container["HTTPS_PROXY"] == "http://user:secret@10.0.2.2:17891"
    for values in (host, container):
        assert "UNRELATED_SECRET" not in values
        assert ".docker.io" not in values["NO_PROXY"]
        for name in ("api", "postgres", "minio", "milvus", "speech", "10.0.0.0/8"):
            assert name in values["NO_PROXY"].split(",")


def test_external_proxy_is_preserved_and_lowercase_proxy_is_supported() -> None:
    host, container = proxy_environments({"https_proxy": "http://proxy.example:8080"})
    assert host["https_proxy"] == container["https_proxy"] == "http://proxy.example:8080"
    assert host["HTTPS_PROXY"] == container["HTTPS_PROXY"] == "http://proxy.example:8080"
    assert container["NO_PROXY"] == container["no_proxy"]
    assert "10.0.2.2" in container["NO_PROXY"].split(",")
