"""Offline command-line entry point."""

import argparse
import json
from pathlib import Path

from . import __version__
from .audit_evaluation import EXIT, evaluate, run_check
from .contracts import Problem
from .prompt_compiler import draft_contract
from .release_gate import issue, prepare


def main(argv=None):
    parser = argparse.ArgumentParser(description="Lokale Belegprüfung mit verbindlicher Freigabesperre.")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    compile_parser = commands.add_parser("compile", help="Auftrag prüfen und Entwurfsvertrag ausgeben")
    check_parser = commands.add_parser("check", help="Entwurf prüfen, Entscheidung speichern und ggf. freigeben")
    for command in (compile_parser, check_parser):
        command.add_argument("--vault", required=True, type=Path)
        command.add_argument("--task", required=True, type=Path)
    check_parser.add_argument("--draft", required=True, type=Path)
    eval_parser = commands.add_parser("evaluate", help="Prüffälle gegen getrennte Sollwerte auswerten")
    eval_parser.add_argument("--suite", required=True, type=Path)
    for command in (check_parser, eval_parser):
        command.add_argument("--audit-dir", required=True, type=Path)

    args = parser.parse_args(argv)
    try:
        if args.command == "compile":
            task, vault = prepare(args.vault, args.task, {})
            result, code = {"status": "DRAFT_CONTRACT", **draft_contract(task, vault)}, 0
        elif args.command == "check":
            result = run_check(args.vault, args.task, args.draft, args.audit_dir)
            code = EXIT[result["status"]]
        else:
            result, code = evaluate(args.suite, args.audit_dir)
    except Problem as exc:
        result, code = {"status": "CHECK_ERROR", "issues": [issue(exc)]}, 2
    except Exception:
        problem = Problem("INTERNAL_ERROR", "Verarbeitung fehlgeschlagen. Keine Freigabe.")
        result, code = {"status": "CHECK_ERROR", "issues": [issue(problem)]}, 2

    print(json.dumps(result, indent=2, ensure_ascii=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
