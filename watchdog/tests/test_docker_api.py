import pytest

from pcbk_watchdog.docker_api import DockerReader, DockerUnavailable


def test_inspect_uses_pinned_api_prefix(fake_proxy):
    DockerReader(fake_proxy.url).inspect("pcbk-sp-ctl")
    assert fake_proxy.paths[-1] == "/v1.44/containers/pcbk-sp-ctl/json"


def test_inspect_404_is_none(fake_proxy):
    assert DockerReader(fake_proxy.url).inspect("pcbk-student-09") is None


def test_inspect_403_raises(fake_proxy):          # прокси отказал — это не «нет контейнера»
    with pytest.raises(DockerUnavailable):
        DockerReader(fake_proxy.url).inspect("pcbk-forbidden")


def test_inspect_rejects_foreign_name_without_request(fake_proxy):
    with pytest.raises(ValueError):
        DockerReader(fake_proxy.url).inspect("../../containers/create")
    assert fake_proxy.paths == []


def test_ping_and_unreachable(fake_proxy):
    DockerReader(fake_proxy.url).ping()                 # не бросает
    with pytest.raises(DockerUnavailable):
        DockerReader("http://127.0.0.1:9", timeout=0.5).ping()
