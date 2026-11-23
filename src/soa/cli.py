"""Command-line interface for a System on an Agent."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from soa import __version__
from soa.addons.loader import AddonError, AddonLoader
from soa.events import Event, read_runs, runs, run_dir
from soa.memory import LongTermMemory, Memory
from soa.system import System


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="soa",
        description="System on an Agent: run a goal through the planner, memory, skills, and connectors.",
    )
    parser.add_argument("--version", action="version", version=f"soa {__version__}")
    parser.add_argument(
        "--addon",
        action="append",
        default=[],
        metavar="PATH",
        help="Load an optional add-on from a Python file or directory.",
    )
    parser.add_argument(
        "--file-root",
        default=None,
        metavar="DIR",
        help="Root directory for the local file connector (default: current directory).",
    )
    parser.add_argument(
        "--memory",
        default=None,
        metavar="FILE",
        help="Persist long-term memory to this JSON file (default: in-process only).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_cmd = sub.add_parser("run", help="Plan and run a goal.")
    run_cmd.add_argument("goal", help="The goal to plan and execute.")
    run_cmd.add_argument(
        "--json",
        action="store_true",
        help="Print the run report as JSON.",
    )
    run_cmd.add_argument(
        "--stream",
        action="store_true",
        help="Print run events (plan_ready, step_started, step_finished, token) as JSONL.",
    )
    run_cmd.add_argument(
        "--no-log",
        action="store_true",
        help="Do not append to the run log (default: append).",
    )

    runs_cmd = sub.add_parser("runs", help="List recorded runs (newest first).")
    runs_cmd.add_argument("--limit", type=int, default=10)
    runs_cmd.add_argument(
        "--events",
        action="store_true",
        help="Print raw event lines instead of one line per run.",
    )

    skills_cmd = sub.add_parser("skills", help="Inspect the skill library.")
    skills_sub = skills_cmd.add_subparsers(dest="skills_command", required=True)
    skills_sub.add_parser("list", help="List registered skills.")

    connectors_cmd = sub.add_parser("connectors", help="Inspect connectors.")
    connectors_sub = connectors_cmd.add_subparsers(dest="connectors_command", required=True)
    connectors_sub.add_parser("list", help="List registered connectors.")

    return parser


def _system_from_args(args: argparse.Namespace) -> System:
    addons = AddonLoader()
    for path in args.addon:
        addons.load_from_path(path)
    file_root = Path(args.file_root) if args.file_root else Path.cwd()
    memory = None
    if args.memory:
        memory = Memory(long_term=LongTermMemory(Path(args.memory)))
    return System(memory=memory, addons=addons, file_root=file_root)


def _print_run(report, *, as_json: bool, out) -> int:
    if as_json:
        json.dump(report.as_dict(), out, indent=2)
        out.write("\n")
        return 0 if report.ok else 1
    out.write(f"goal: {report.goal}\n")
    out.write(f"plan: {len(report.plan.steps)} step")
    out.write("s" if len(report.plan.steps) != 1 else "")
    out.write(f" ({report.plan.source})\n")
    for index, step in enumerate(report.plan.steps, start=1):
        out.write(f"  {index}. {step.description or step.action}\n")
    out.write(f"ok: {str(report.ok).lower()}\n")
    if report.skipped:
        out.write(f"skipped: {report.skipped} step(s) after the failure\n")
    if report.output:
        out.write(f"output: {report.output}\n")
    elif not report.ok:
        errors = [r.error for r in report.results if r.error]
        if errors:
            out.write(f"error: {errors[-1]}\n")
    return 0 if report.ok else 1


def main(argv: Sequence[str] | None = None, out=None, err=None) -> int:
    out = out or sys.stdout
    err = err or sys.stderr
    parser = build_parser()
    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
        system = _system_from_args(args)
    except AddonError as exc:
        err.write(f"soa: {exc}\n")
        return 2
    except SystemExit as exc:  # argparse --help / --version / usage
        return int(exc.code or 0)

    if args.command == "run":
        akış = None
        if args.stream:

            def akış(olay: Event) -> None:
                out.write(json.dumps(olay.as_dict(), ensure_ascii=False) + "\n")
                out.flush()

        report = system.run(args.goal, on_event=akış, run_log=False if args.no_log else None)
        return _print_run(report, as_json=args.json, out=out)
    if args.command == "runs":
        if args.events:
            for satir in read_runs(limit=args.limit):
                out.write(json.dumps(satir, ensure_ascii=False) + "\n")
            return 0
        kayitlar = runs(limit=args.limit)
        if not kayitlar:
            out.write(f"(kayıt yok: {run_dir()})\n")
            return 0
        for kayit in kayitlar:
            durum = "ok" if kayit.get("ok") else (kayit.get("error_class") or "—")
            out.write(
                f"{kayit.get('started')}  {kayit.get('run_id')}  "
                f"{kayit.get('steps')} adım  {durum}  {kayit.get('goal') or ''}\n"
            )
        return 0
    if args.command == "skills" and args.skills_command == "list":
        for skill in system.skills.list():
            out.write(f"{skill.name}: {skill.description}\n")
        return 0
    if args.command == "connectors" and args.connectors_command == "list":
        for connector in system.connectors.list():
            out.write(f"{connector.name} ({connector.kind})\n")
        return 0
    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
