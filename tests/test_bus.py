from __future__ import annotations

from pathlib import Path

from soa.bus import Bus
from soa.connectors import ConnectorRegistry, EchoConnector, FileConnector
from soa.memory import Memory
from soa.skills import Skill, SkillLibrary, builtin_skills
from soa.types import Message, Result


def make_bus(tmp_path: Path | None = None) -> Bus:
    skills = SkillLibrary()
    for skill in builtin_skills():
        skills.register(skill)
    skills.register(Skill("ping", "return pong", lambda: "pong"))
    connectors = ConnectorRegistry(
        [EchoConnector(), FileConnector(root=tmp_path or Path.cwd())]
    )
    return Bus(Memory(), skills, connectors)


def test_bus_routes_memory_remember_and_recall():
    bus = make_bus()
    stored = bus.dispatch(Message("memory", "remember", {"fact": "rivers flow"}))
    assert stored.ok is True
    hit = bus.dispatch(Message("memory", "recall", {"query": "rivers"}))
    assert hit.data == "rivers flow"


def test_bus_working_memory_set_and_get():
    bus = make_bus()
    bus.dispatch(Message("memory", "set", {"key": "n", "value": 3}))
    got = bus.dispatch(Message("memory", "get", {"key": "n"}))
    assert got.data == 3


def test_bus_unknown_channel_and_actions():
    bus = make_bus()
    assert bus.dispatch(Message("nope", "x")).ok is False
    assert bus.dispatch(Message("memory", "drop")).ok is False
    assert bus.dispatch(Message("skill", "delete")).ok is False
    assert bus.dispatch(Message("connector", "send", {"connector": "echo"})).ok is True
    assert bus.dispatch(Message("connector", "wave", {"connector": "echo"})).ok is False
    assert bus.dispatch(Message("system", "reboot")).ok is False


def test_bus_dispatches_skill_and_expands_echo():
    bus = make_bus()
    result = bus.dispatch(Message("skill", "run", {"name": "echo", "text": "hello"}))
    assert result.ok is True
    assert result.data == "hello"
    listed = bus.dispatch(Message("skill", "list"))
    assert "echo" in listed.data
    custom = bus.dispatch(Message("skill", "run", {"name": "ping"}))
    assert custom.data == "pong"


def test_bus_connector_send_and_file_call(tmp_path: Path):
    bus = make_bus(tmp_path)
    echoed = bus.dispatch(
        Message("connector", "send", {"connector": "echo", "payload": "ping"})
    )
    assert echoed.data == "ping"
    written = bus.dispatch(
        Message(
            "connector",
            "call",
            {"connector": "file", "method": "write", "path": "f.txt", "content": "x"},
        )
    )
    assert written.ok is True
    read = bus.dispatch(
        Message("connector", "call", {"connector": "file", "method": "read", "path": "f.txt"})
    )
    assert read.data == "x"


def test_bus_missing_connector_name():
    bus = make_bus()
    result = bus.dispatch(Message("connector", "send", {}))
    assert result.ok is False
    missing = bus.dispatch(Message("connector", "send", {"connector": "nope"}))
    assert "unknown connector" in missing.error


def test_bus_system_lists_and_noop():
    bus = make_bus()
    skills = bus.dispatch(Message("system", "list_skills"))
    assert "remember" in skills.data
    connectors = bus.dispatch(Message("system", "list_connectors"))
    assert any(item.startswith("echo") for item in connectors.data)
    assert bus.dispatch(Message("system", "noop")).ok is True


def test_dispatch_step_translates_planner_actions():
    bus = make_bus()
    result = bus.dispatch_step(
        "connector.send", {"connector": "echo", "payload": "step"}
    )
    assert result.data == "step"
    unknown = bus.dispatch_step("widget.spin", {})
    assert unknown.ok is False


def test_builtin_skill_expansion_for_memory_and_files(tmp_path: Path):
    bus = make_bus(tmp_path)
    bus.dispatch(Message("skill", "run", {"name": "remember", "text": "stored"}))
    recalled = bus.dispatch(Message("skill", "run", {"name": "recall", "query": "stored"}))
    assert recalled.data == "stored"
    bus.dispatch(
        Message("skill", "run", {"name": "write_file", "path": "a.txt", "content": "z"})
    )
    read = bus.dispatch(Message("skill", "run", {"name": "read_file", "path": "a.txt"}))
    assert read.data == "z"


def test_connector_is_called_once_when_it_raises_type_error(tmp_path: Path):
    class Fragile:
        name = "fragile"
        kind = "test"

        def __init__(self) -> None:
            self.calls = 0

        def send(self, payload, **kwargs):
            self.calls += 1
            raise TypeError("real bug inside the connector")

    connector = Fragile()
    bus = Bus(Memory(), SkillLibrary(), ConnectorRegistry([connector]))
    result = bus.dispatch_step("connector.send", {"connector": "fragile", "payload": "x"})
    assert connector.calls == 1
    assert result.ok is False
    assert result.action == "connector.send"
    assert result.error == "TypeError: real bug inside the connector"
    assert isinstance(result.exception, TypeError)


def test_dispatch_step_reports_a_raising_step_as_a_failed_result():
    class Broken:
        name = "broken"
        kind = "test"

        def send(self, payload, **kwargs):
            raise RuntimeError("connector exploded")

    bus = Bus(Memory(), SkillLibrary(), ConnectorRegistry([Broken()]))
    result = bus.dispatch_step("connector.send", {"connector": "broken", "payload": "x"})
    assert result.ok is False
    assert "connector exploded" in (result.error or "")
    assert isinstance(result.exception, RuntimeError)


def test_dispatch_step_catches_a_failure_raised_by_a_nested_dispatch():
    class ExplodingEcho:
        name = "echo"
        kind = "test"

        def send(self, payload, **kwargs):
            raise RuntimeError("echo exploded")

    skills = SkillLibrary()
    for skill in builtin_skills():
        skills.register(skill)
    # The builtin `echo` skill re-dispatches onto the "echo" connector, so the
    # exception is raised one dispatch level below dispatch_step.
    bus = Bus(Memory(), skills, ConnectorRegistry([ExplodingEcho()]))
    result = bus.dispatch_step("skill.run", {"name": "echo", "text": "hi"})
    assert result.ok is False
    assert "echo exploded" in (result.error or "")
    assert isinstance(result.exception, RuntimeError)


def test_named_connector_keeps_the_rest_of_the_payload(tmp_path: Path):
    class Recorder:
        name = "recorder"
        kind = "test"

        def __init__(self) -> None:
            self.seen: dict = {}

        def send(self, payload, **kwargs):
            self.seen = kwargs
            return Result(ok=True, data=payload)

    recorder = Recorder()
    bus = Bus(Memory(), SkillLibrary(), ConnectorRegistry([recorder]))
    bus.dispatch_step(
        "connector.send", {"connector": "recorder", "name": "a.txt", "payload": "x"}
    )
    assert recorder.seen == {"name": "a.txt"}


def test_connector_without_kwargs_still_accepts_extra_payload(tmp_path: Path):
    class Narrow:
        name = "narrow"
        kind = "test"

        def send(self, payload):
            return Result(ok=True, data=payload)

    bus = Bus(Memory(), SkillLibrary(), ConnectorRegistry([Narrow()]))  # type: ignore[list-item]
    result = bus.dispatch_step(
        "connector.send", {"connector": "narrow", "payload": "x", "unused": 1}
    )
    assert result.data == "x"


def test_bus_keeps_an_injected_empty_addon_loader():
    from soa.addons import AddonLoader

    loader = AddonLoader()
    assert not loader  # empty loader is falsy: `addons or AddonLoader()` used to replace it
    bus = Bus(Memory(), SkillLibrary(), ConnectorRegistry([]), addons=loader)
    assert bus.addons is loader
