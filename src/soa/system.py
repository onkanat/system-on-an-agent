"""Assemble the core parts of a System on an Agent."""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from soa.addons.interface import HookContext
from soa.addons.loader import AddonLoader
from soa.bus import Bus
from soa.connectors import ConnectorRegistry, EchoConnector, FileConnector
from soa.memory import Memory
from soa.planner import LLMProvider, Plan, Planner, result_output
from soa.skills import SkillLibrary, builtin_skills
from soa.events import Event, EventSink, RunLog, compose, new_run_id
from soa.types import Result as BusResult
from soa.types import classify_error


@dataclass
class RunReport:
    """Outcome of running a goal through the system."""

    goal: str
    plan: Plan
    results: list[BusResult] = field(default_factory=list)
    ok: bool = True
    # Koşu kaydındaki satırlarla eşleştirme anahtarı (RunLog + delta-iz).
    run_id: str | None = None

    @property
    def skipped(self) -> int:
        """Planned steps that never ran because an earlier step failed."""
        return max(0, len(self.plan.steps) - len(self.results))

    @property
    def output(self) -> str:
        return result_output(self.results)

    @property
    def error_class(self) -> str | None:
        """Düşen adımın SINIFI (``ERROR_CLASSES``) — çağıran metin eşleştirmesin.

        Ölçüldü (23.11.2026): tüketici "connection refused" gibi metne bakıyordu;
        sağlayıcı mesajı değişince geri düşme kararı sessizce bozuluyordu.
        """
        for sonuc in reversed(self.results):
            if not sonuc.ok:
                return sonuc.error_class
        return None

    def as_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "run_id": self.run_id,
            "ok": self.ok,
            "skipped": self.skipped,
            "source": self.plan.source,
            "fallback_reason": self.plan.fallback_reason,
            "error_class": self.error_class,
            "steps": [
                {
                    "action": step.action,
                    "args": step.args,
                    "description": step.description,
                }
                for step in self.plan.steps
            ],
            "results": [
                {
                    "ok": result.ok,
                    "data": _jsonable(result.data),
                    "error": result.error,
                    "error_class": result.error_class,
                    "action": result.action,
                }
                for result in self.results
            ],
            "output": self.output,
        }


def _planla(planner, goal: str, names: list[str], on_token):
    """Plan üret; ``on_token`` destekleyen planlayıcıya ilet, eskisini bozma.

    Üçüncü taraf planlayıcılar ``plan(goal, skill_names)`` imzasıyla yazılmış
    olabilir; akış uğruna onları kırmak doğru değil (aşama 4 geriye uyumlu kalır).
    """
    try:
        imza = inspect.signature(planner.plan)
    except (TypeError, ValueError):  # C ile yazılmış/çağrılabilir nesne
        return planner.plan(goal, names)
    if "on_token" in imza.parameters:
        return planner.plan(goal, names, on_token=on_token)
    return planner.plan(goal, names)


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return str(value)


class System:
    """The assembled agent: planner, memory, skills, connectors, and bus."""

    def __init__(
        self,
        *,
        memory: Memory | None = None,
        skills: SkillLibrary | None = None,
        connectors: ConnectorRegistry | None = None,
        planner: Planner | None = None,
        addons: AddonLoader | None = None,
        llm: LLMProvider | None = None,
        file_root: str | Path | None = None,
        run_log: RunLog | None | bool = True,
    ) -> None:
        # `x or Default()` is unsafe here: SkillLibrary, ConnectorRegistry and
        # AddonLoader all define __len__, so an empty-but-configured instance is
        # falsy and would be silently replaced by a fresh default.
        self.memory = Memory() if memory is None else memory
        self.skills = SkillLibrary() if skills is None else skills
        if skills is None:
            for skill in builtin_skills():
                self.skills.register(skill)
        if connectors is None:
            connectors = ConnectorRegistry(
                [
                    EchoConnector(),
                    FileConnector(root=Path(file_root) if file_root else Path.cwd()),
                ]
            )
        self.connectors = connectors
        self.addons = AddonLoader() if addons is None else addons
        self.planner = Planner(llm=llm) if planner is None else planner
        self.bus = Bus(self.memory, self.skills, self.connectors, self.addons)
        # Koşu kaydı: varsayılan AÇIK (koşular yeniden üretilebilsin). Testler
        # SOA_RUN_DIR ile kendi dizinine yönlendirir; run_log=False kapatır.
        if run_log is True:
            self.run_log: RunLog | None = RunLog()
        elif run_log is False or run_log is None:
            self.run_log = None
        else:
            self.run_log = run_log

    def run(
        self,
        goal: str,
        *,
        on_event: EventSink | None = None,
        run_log: "RunLog | None | bool" = None,
    ) -> RunReport:
        """Plan a goal and execute each step on the bus, firing add-on hooks.

        ``on_event`` olayları ANINDA alır (akış); verilmezse yalnız koşu kaydı
        yazılır. ``run_log`` çağrı başına geçersiz kılar: ``True`` aç, ``False``
        kapat, ``RunLog`` kendi dosyanı ver.
        """
        run_id = new_run_id()
        if run_log is None:
            kayit = self.run_log
        elif run_log is True:
            kayit = RunLog()
        elif run_log is False:
            kayit = None
        else:
            kayit = run_log
        sink = compose(on_event, kayit)

        def emit(kind: str, **alanlar) -> None:
            sink(Event(kind=kind, run_id=run_id, **alanlar))

        context = HookContext(goal=goal)

        def _token(parça: str) -> None:
            if parça:
                emit("token", text=parça)

        plan = _planla(self.planner, goal, self.skills.names(), _token if sink else None)
        plan = self.addons.fire_before_plan(plan, context)
        emit(
            "plan_ready",
            data={
                "goal": goal,
                "steps": len(plan.steps),
                "source": plan.source,
                "fallback_reason": plan.fallback_reason,
            },
        )
        results: list[BusResult] = []
        ok = True
        for index, raw_step in enumerate(plan.steps, start=1):
            step = self.addons.fire_before_step(raw_step, context)
            emit("step_started", index=index, action=step.action, data={"args": step.args})
            try:
                result = self.bus.dispatch_step(step.action, step.args)
            except Exception as exc:  # noqa: BLE001 - a custom bus may still raise
                self.addons.fire_on_error(step, exc, context)
                result = BusResult(ok=False, error=str(exc), action=step.action,
                                   error_class=classify_error(exc))
            else:
                # The default bus reports a raising step as Result(ok=False)
                # and carries the exception, so the hook keeps seeing it.
                raised = getattr(result, "exception", None)
                if raised is not None:
                    self.addons.fire_on_error(step, raised, context)
            result = self.addons.fire_after_step(step, result, context)
            results.append(result)
            emit(
                "step_finished",
                index=index,
                action=step.action,
                ok=bool(result.ok),
                error=result.error,
                error_class=result.error_class,
            )
            if not result.ok:
                ok = False
                break
        report = RunReport(goal=goal, plan=plan, results=results, ok=ok, run_id=run_id)
        rapor = self.addons.fire_after_plan(plan, report, context)
        emit(
            "run_finished",
            ok=bool(rapor.ok),
            error_class=getattr(rapor, "error_class", None),
            data={"steps": len(results), "skipped": getattr(rapor, "skipped", 0)},
        )
        return rapor
