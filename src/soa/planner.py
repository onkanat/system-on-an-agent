"""Planner: the processor of a System on an Agent.

The default planner is deterministic and rule-based. No language model
is required. An optional LLM provider may be attached; if it fails to
produce a valid plan, the rule-based planner is used as the fallback.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterable

from soa.types import Result, classify_error


@dataclass(frozen=True)
class Step:
    """One action in a plan."""

    action: str
    args: dict[str, Any] = field(default_factory=dict)
    description: str = ""


@dataclass
class Plan:
    """An ordered list of steps for a goal."""

    goal: str
    steps: list[Step]
    source: str = "rules"
    # Sağlayıcı planı üretemediğinde NEDEN kural planına düşüldüğü:
    # "provider" (çağrı yükseldi), "protocol" (geçersiz/boş JSON), None (düşmedi).
    fallback_reason: str | None = None


class LLMProvider(ABC):
    """Optional language-model backend. Not required for the core planner."""

    @abstractmethod
    def complete(self, prompt: str, *, system: str | None = None) -> str:
        """Return a model completion for the given prompt."""

    def stream(self, prompt: str, *, system: str | None = None):
        """Parça parça cevap. Varsayılan: akışı olmayan sağlayıcı tek parça verir.

        Akışı olan sağlayıcı (SSE/HTTP chunked) bunu ezer; `System.run` üretilen her
        parçayı `token` olayı olarak yayar. Varsayılanın tek parça olması bilinçli:
        sağlayıcı akış sunmuyorsa uydurma parçalama yapılmaz.
        """
        yield self.complete(prompt, system=system)


# "then" is the explicit sequencer. "and then" is also accepted.
_SPLIT = re.compile(r"\s+(?:and\s+then|then)\s+", re.IGNORECASE)

_ECHO = re.compile(r"^(?:echo|say)\s+(.+)$", re.IGNORECASE)
_REMEMBER = re.compile(r"^(?:remember|store|memorize)\s+(.+)$", re.IGNORECASE)
_RECALL = re.compile(
    r"^(?:recall|remember\s+what|what do you know about|what about)\s+(.+)$",
    re.IGNORECASE,
)
_READ = re.compile(r"^(?:read(?:_file)?|open)\s+(.+)$", re.IGNORECASE)
_WRITE_TO = re.compile(
    r"^(?:write(?:_file)?)\s+(.+?)\s+to\s+(.+)$",
    re.IGNORECASE,
)
_WRITE_COLON = re.compile(
    r"^(?:write(?:_file)?)\s+(\S+)\s*[:]\s*(.+)$",
    re.IGNORECASE,
)
_LIST_SKILLS = re.compile(r"^(?:list\s+skills|skills(?:\s+list)?)$", re.IGNORECASE)
_LIST_CONNECTORS = re.compile(
    r"^(?:list\s+connectors|connectors(?:\s+list)?)$",
    re.IGNORECASE,
)
_RUN_SKILL = re.compile(r"^(?:run|use)\s+(?:skill\s+)?(\S+)(?:\s+(.*))?$", re.IGNORECASE)

# Channels the bus actually routes. A plan that names anything else can only
# fail at dispatch time, so the planner rejects it up front.
_KNOWN_ACTION_PREFIXES = ("memory.", "skill.", "connector.", "system.")


class Planner:
    """Turn a goal into a plan and (optionally) help execute it."""

    def __init__(self, llm: LLMProvider | None = None) -> None:
        self.llm = llm
        # Son `plan()` çağrısında kural planına düşüldüyse SEBEBİ (ERROR_CLASSES).
        self._fallback_reason: str | None = None

    def plan(
        self,
        goal: str,
        skill_names: Iterable[str] | None = None,
        *,
        on_token=None,
    ) -> Plan:
        """Build a plan for ``goal``.

        If an LLM provider is attached, it is tried first. Any failure
        or invalid response falls back to the deterministic planner.
        """
        names = [n.lower() for n in (skill_names or [])]
        if self.llm is not None:
            self._fallback_reason = None
            llm_plan = self._plan_with_llm(goal, names, on_token=on_token)
            if llm_plan is not None:
                return llm_plan
            reason = getattr(self, "_fallback_reason", "protocol")
        else:
            reason = None
        return Plan(goal=goal, steps=self._plan_with_rules(goal, names),
                    source="rules", fallback_reason=reason)

    def describe(self, plan: Plan) -> list[str]:
        return [
            step.description or f"{step.action} {step.args}".strip()
            for step in plan.steps
        ]

    def _plan_with_rules(self, goal: str, skill_names: list[str]) -> list[Step]:
        text = goal.strip()
        if not text:
            return [
                Step(
                    action="system.noop",
                    args={},
                    description="empty goal",
                )
            ]
        parts = [p.strip() for p in _SPLIT.split(text) if p.strip()]
        return [self._parse_clause(part, skill_names) for part in parts]

    def _parse_clause(self, clause: str, skill_names: list[str]) -> Step:
        if match := _LIST_SKILLS.match(clause):
            return Step("system.list_skills", {}, "list registered skills")
        if match := _LIST_CONNECTORS.match(clause):
            return Step("system.list_connectors", {}, "list registered connectors")
        if match := _ECHO.match(clause):
            text = match.group(1).strip().strip("\"'")
            return Step(
                "connector.send",
                {"connector": "echo", "payload": text},
                f'Send "{text}" through the echo connector',
            )
        if match := _RECALL.match(clause):
            query = match.group(1).strip().strip("\"'")
            return Step(
                "memory.recall",
                {"query": query},
                f'recall facts matching "{query}"',
            )
        if match := _REMEMBER.match(clause):
            fact = match.group(1).strip().strip("\"'")
            return Step(
                "memory.remember",
                {"fact": fact},
                f'remember "{fact}"',
            )
        if match := _WRITE_TO.match(clause):
            content = match.group(1).strip().strip("\"'")
            path = match.group(2).strip().strip("\"'")
            return Step(
                "connector.call",
                {"connector": "file", "method": "write", "path": path, "content": content},
                f'write to {path}',
            )
        if match := _WRITE_COLON.match(clause):
            path = match.group(1).strip().strip("\"'")
            content = match.group(2).strip().strip("\"'")
            return Step(
                "connector.call",
                {"connector": "file", "method": "write", "path": path, "content": content},
                f'write to {path}',
            )
        if match := _READ.match(clause):
            path = match.group(1).strip().strip("\"'")
            return Step(
                "connector.call",
                {"connector": "file", "method": "read", "path": path},
                f"read {path}",
            )
        if match := _RUN_SKILL.match(clause):
            name = match.group(1)
            rest = (match.group(2) or "").strip()
            args = {"name": name}
            if rest:
                args["text"] = rest
            return Step(
                "skill.run",
                args,
                f"run skill {name}",
            )

        first = clause.split(None, 1)
        head = first[0].lower() if first else ""
        rest = first[1] if len(first) == 2 else ""
        if head in skill_names:
            args: dict[str, Any] = {"name": head}
            if rest:
                args["text"] = rest
            return Step("skill.run", args, f"run skill {head}")

        return Step(
            "connector.send",
            {"connector": "echo", "payload": clause},
            f'Send "{clause}" through the echo connector',
        )

    def _plan_with_llm(self, goal: str, skill_names: list[str], *, on_token=None) -> Plan | None:
        system = (
            "You are the planner for a System on an Agent. "
            "Return only a JSON array of steps. Each step is an object with "
            'keys "action", "args" (object), and "description" (string). '
            f"Known skills: {', '.join(skill_names) or '(none)'}."
        )
        try:
            if on_token is None:
                raw = self.llm.complete(f"Goal: {goal}", system=system)
            else:
                # Akış: sağlayıcı parça parça veriyorsa her parça ANINDA iletilir,
                # plan yine tam metinden kurulur (akış planı bozmaz).
                parçalar: list[str] = []
                for parça in self.llm.stream(f"Goal: {goal}", system=system):
                    parçalar.append(parça)
                    on_token(parça)
                raw = "".join(parçalar)
            data = json.loads(raw)
        except Exception as exc:  # noqa: BLE001 - any provider failure means "fall back"
            # A provider that raises (network down, timeout, rate limit, SDK
            # error) is exactly the case the rule-based planner exists for.
            # NEDEN düşüldüğü sınıf olarak saklanır: tüketici metin eşleştirmesi
            # yapmak zorunda kalmasın (sağlayıcı mesajı değişince karar kaymasın).
            if isinstance(exc, json.JSONDecodeError):
                self._fallback_reason = "protocol"
            else:
                sinif = classify_error(exc)
                # Sağlayıcıdan gelen her hata geri düşme sebebidir; sınıf bilinmiyorsa
                # "provider" yazılır (çağrı sağlayıcıdaydı, kural planı devraldı).
                self._fallback_reason = "provider" if sinif in {"unknown", "connector"} else sinif
            return None
        if not isinstance(data, list) or not data:
            self._fallback_reason = "protocol"
            return None
        steps: list[Step] = []
        for item in data:
            if not isinstance(item, dict) or "action" not in item:
                return None
            action = str(item["action"])
            if not action.startswith(_KNOWN_ACTION_PREFIXES):
                return None
            args = item.get("args") or {}
            if not isinstance(args, dict):
                return None
            steps.append(
                Step(
                    action=action,
                    args=args,
                    description=str(item.get("description") or action),
                )
            )
        return Plan(goal=goal, steps=steps, source="llm")


def result_output(results: list[Result]) -> str:
    """Join step results into a single display string."""
    if not results:
        return ""
    return "\n".join(r.text() for r in results)
