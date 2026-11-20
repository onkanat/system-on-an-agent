from __future__ import annotations

from pathlib import Path

from soa.addons.interface import AddonManifest
from soa.addons.loader import AddonLoader
from soa.connectors import ConnectorRegistry
from soa.planner import Plan, Planner, Step
from soa.skills import Skill, SkillLibrary
from soa.system import System


def test_injected_empty_skill_library_is_kept(tmp_path: Path):
    skills = SkillLibrary()  # empty, and therefore falsy
    system = System(skills=skills, file_root=tmp_path)
    assert system.skills is skills
    skills.register(Skill("greet", "say hi", lambda text="": f"hi {text}"))
    assert system.skills.has("greet")


def test_injected_empty_connector_registry_is_kept(tmp_path: Path):
    connectors = ConnectorRegistry()
    system = System(connectors=connectors, file_root=tmp_path)
    assert system.connectors is connectors


def test_injected_empty_addon_loader_is_kept(tmp_path: Path):
    addons = AddonLoader()
    system = System(addons=addons, file_root=tmp_path)
    assert system.addons is addons


def test_report_counts_the_steps_skipped_after_a_failure(tmp_path: Path):
    system = System(file_root=tmp_path)
    report = system.run("read missing.txt then echo never")
    assert report.ok is False
    assert len(report.results) == 1
    assert report.skipped == 1
    assert report.as_dict()["skipped"] == 1


def test_raising_connector_is_reported_to_the_run_and_the_on_error_hook(tmp_path: Path):
    class Boom:
        name = "boom"
        kind = "test"

        def send(self, payload, **kwargs):
            raise RuntimeError("boom")

    class OneStepPlanner(Planner):
        def plan(self, goal, skill_names=None):  # type: ignore[override]
            return Plan(
                goal=goal,
                steps=[Step("connector.send", {"connector": "boom", "payload": "x"})],
                source="test",
            )

    class Probe:
        manifest = AddonManifest(name="probe", version="0", hooks=("on_error",))

        def __init__(self) -> None:
            self.errors: list[tuple[str, str]] = []

        def on_error(self, step, error, context):
            self.errors.append((step.action, str(error)))

    loader = AddonLoader()
    probe = Probe()
    loader.add(probe)
    system = System(
        connectors=ConnectorRegistry([Boom()]),  # type: ignore[list-item]
        addons=loader,
        file_root=tmp_path,
    )
    system.planner = OneStepPlanner()
    report = system.run("boom now")
    assert report.ok is False
    assert report.results[0].error == "RuntimeError: boom"
    assert report.results[0].exception is not None
    assert probe.errors == [("connector.send", "boom")]

