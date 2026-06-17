import os
import pathlib
import pytest
from unittest.mock import AsyncMock, patch

from quartermaster import provision_env


FIXTURES = pathlib.Path(__file__).parent / "fixtures"


class _Stream:
    def __init__(self):
        self.events = []

    def append(self, ev):
        self.events.append(ev)


async def _fake_run_deploy(env, service, snapshot):
    yield {"type": "log", "data": f"stub deploy {service}@{snapshot} -> {env}"}
    yield {"type": "done", "success": True}


@pytest.mark.asyncio
async def test_full_provision_cycle_emits_expected_event_sequence(monkeypatch):
    monkeypatch.setenv("PATH", str(FIXTURES) + ":" + os.environ["PATH"])
    monkeypatch.setenv("QM_STUB_ENV_EXISTS", "0")
    # Symlink the stub so PATH lookup finds 'deploycli'. `Path.exists()` follows
    # symlinks, so a dangling link from a prior run would short-circuit the
    # guard and crash `symlink_to`. Use `lstat` + unlink to be rerun-safe.
    stub = FIXTURES / "stub_ivy.sh"
    ivy_link = FIXTURES / "deploycli"
    try:
        ivy_link.lstat()
        ivy_link.unlink()
    except FileNotFoundError:
        pass
    ivy_link.symlink_to(stub)

    stream = _Stream()
    prs = [{"repo": "service-a", "branch": "feature/PROJ-404", "snapshot": "FEATURE-PROJ-404"}]

    with patch("quartermaster.bb.get_file",
               AsyncMock(return_value=(FIXTURES / "manifest_service.json").read_text())), \
         patch("quartermaster.agents.snapshot_artifact_exists",
               AsyncMock(return_value=("exists", None, []))), \
         patch("quartermaster.agents.run_deploy", _fake_run_deploy):
        result = await provision_env("PROJ-404", prs, stream)

    assert result == {"status": "ok"}

    types = [e["type"] for e in stream.events]
    assert "stage_change" in types
    assert "log" in types
    assert types[-1] == "done"
    assert stream.events[-1]["status"] == "ok"
