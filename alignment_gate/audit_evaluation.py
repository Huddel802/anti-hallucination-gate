"""Persist a decision before output, and evaluate against separate labels."""

import hashlib
import json
import os
import tempfile
import uuid
from collections import Counter
from datetime import datetime, timezone

from . import __version__
from .contracts import Key, Problem, document, identifier, iso_day, items, parse_json, plain_text, read_local, shape
from .release_gate import decide, issue

EXIT = {"RELEASED": 0, "BLOCKED": 1, "CHECK_ERROR": 2}


def _audit_write(directory, audit_id, event):
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=directory, prefix=".pending-")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(event, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, directory / f"{audit_id}.json")
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def run_check(vault_path, task_path, draft_path, audit_dir):
    decision, trace = decide(vault_path, task_path, draft_path)
    audit_id = uuid.uuid4().hex
    event = {
        "schema": 1, "version": __version__, "audit_id": audit_id,
        "time_utc": datetime.now(timezone.utc).isoformat(),
        "status": decision["status"], "inputs_sha256": trace,
        "issues": decision.get("issues", []),
    }
    if "phase" in decision:
        event["phase"] = decision["phase"]
    if decision["status"] == "RELEASED":
        answer = decision["answer"]
        canonical = json.dumps(answer, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        event["answer_sha256"] = hashlib.sha256(canonical.encode("ascii")).hexdigest()
        event["claims"] = [
            {
                "target": {key: fact[key] for key in ("subject", "field", "as_of")},
                "method": fact["method"],
                "references": [{key: ref[key] for key in ("source", "line", "role")} for ref in fact["citations"]],
            }
            for fact in answer
        ]
    try:
        _audit_write(audit_dir, audit_id, event)
    except OSError:
        problem = Problem("AUDIT_WRITE_ERROR", "Protokoll konnte nicht gespeichert werden. Freigabe abgebrochen.")
        return {"status": "CHECK_ERROR", "issues": [issue(problem)]}
    return {**decision, "audit_id": audit_id}


def _ratio(count, total):
    return {"count": count, "total": total, "rate": count / total if total else None}


def _fact_signature(fact):
    value = fact["value"]
    return (*(fact[key] for key in Key._fields), type(value), value)


def _expected_facts(rows, expected):
    if expected != "RELEASED":
        if rows is not None:
            raise Problem("INVALID_SUITE", "Für gesperrte Fälle muss expected_answer null sein.")
        return Counter()
    target_keys = set()
    facts = Counter()
    for row in items(rows, "Freigegebene Referenzclaims"):
        shape(row, (*Key._fields, "value"), "Referenzclaim")
        key = Key(
            identifier(row["subject"], "subject"),
            identifier(row["field"], "field"),
            iso_day(row["as_of"], "as_of"),
        )
        if key in target_keys or type(row["value"]) not in (int, str):
            raise Problem("INVALID_SUITE", "Doppeltes Referenzziel oder falscher Referenzwerttyp.")
        if isinstance(row["value"], str):
            plain_text(row["value"], "Referenzwert")
        target_keys.add(key)
        facts[_fact_signature(row)] += 1
    return facts


def evaluate(suite_path, audit_dir):
    raw, digest = read_local(suite_path)
    document_data = parse_json(raw)
    document(document_data, ("cases",), "Evaluation")
    cases = []
    ids = set()
    for case in items(document_data["cases"], "Evaluationsfälle"):
        shape(case, ("id", "vault", "task", "draft", "expected_status", "expected_issue",
                     "expected_answer", "basis"), "Fall")
        case_id = identifier(case["id"], "Fall-ID")
        expected = case["expected_status"]
        expected_issue = case["expected_issue"]
        if (case_id in ids or not isinstance(expected, str) or expected not in EXIT
                or (expected == "RELEASED") != (expected_issue is None)):
            raise Problem("INVALID_SUITE", "Fall-ID oder Referenzstatus ist ungültig.")
        if expected_issue is not None:
            plain_text(expected_issue, "Referenzfehlercode", 80)
        ids.add(case_id)
        paths = [suite_path.parent / plain_text(case[name], name) for name in ("vault", "task", "draft")]
        basis = plain_text(case["basis"], "Fallherkunft")
        gold_facts = _expected_facts(case["expected_answer"], expected)
        cases.append((case_id, paths, expected, expected_issue, gold_facts, basis))

    results = []
    for case_id, paths, expected, expected_issue, gold_facts, basis in cases:
        result = run_check(*paths, audit_dir)
        codes = [problem["code"] for problem in result.get("issues", [])]
        actual = result["status"]
        released = Counter(_fact_signature(fact) for fact in result.get("answer", []))
        extra = sum((released - gold_facts).values())
        missing = sum((gold_facts - released).values())
        results.append({
            "id": case_id, "basis": basis, "expected": expected, "expected_issue": expected_issue,
            "actual": actual, "issue_codes": codes, "audit_id": result.get("audit_id"),
            "released_claims": sum(released.values()), "claims_outside_reference": extra,
            "missing_reference_claims": missing,
            "matched": (
                actual == expected and (expected_issue is None or expected_issue in codes)
                and extra == 0 and missing == 0
            ),
        })
    expected_release = [case for case in results if case["expected"] == "RELEASED"]
    expected_hold = [case for case in results if case["expected"] != "RELEASED"]
    total = len(results)
    report = {
        "suite_sha256": digest,
        "metrics": {
            "matched_cases": _ratio(sum(case["matched"] for case in results), total),
            "answer_rate": _ratio(sum(case["actual"] == "RELEASED" for case in results), total),
            "false_releases": _ratio(sum(case["actual"] == "RELEASED" for case in expected_hold), len(expected_hold)),
            "false_blocks": _ratio(sum(case["actual"] != "RELEASED" for case in expected_release), len(expected_release)),
            "released_claims_outside_reference": _ratio(
                sum(case["claims_outside_reference"] for case in results),
                sum(case["released_claims"] for case in results),
            ),
            "check_errors": _ratio(sum(case["actual"] == "CHECK_ERROR" for case in results), total),
        },
        "cases": results,
    }
    return report, 0 if all(case["matched"] for case in results) else 1
