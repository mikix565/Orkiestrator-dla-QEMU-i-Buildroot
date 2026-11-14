import pytest


@pytest.fixture(autouse=True)
def no_idle_probe(monkeypatch):
    monkeypatch.setattr("orchestrator.experiment.idle_probe", lambda *args, **kwargs: 0.5)
