"""The sole path from an untrusted draft to a releasable response."""

from .claim_extractor import extract_claims
from .consistency_engine import check_consistency
from .contracts import Problem, parse_json, read_local
from .evidence_vault import Vault, load_manifest
from .evidence_verifier import require_supported_value, verify_evidence
from .prompt_compiler import compile_task


def _input(path, label, trace):
    try:
        raw, digest = read_local(path)
        trace[label] = digest
        return parse_json(raw)
    except Problem as exc:
        raise Problem(exc.code, f"Eingabe {label}. {exc}") from exc


def prepare(vault_path, task_path, trace):
    fields, sources = load_manifest(_input(vault_path, "vault", trace))
    task = compile_task(_input(task_path, "task", trace), fields, sources)
    trace["pinned_sources"] = {name: sources[name].sha256 for name in task.allowed_sources}
    vault = Vault(vault_path.parent, fields, sources, task.allowed_sources)
    trace["verified_sources"] = vault.loaded_hashes
    return task, vault


def issue(problem, key=None):
    record = {"code": problem.code, "message": str(problem)}
    if key is not None:
        record["target"] = key._asdict()
    if problem.references:
        record["references"] = [ref._asdict() for ref in problem.references]
    return record


def _check_claims(task, vault, claims):
    problems = []
    unexpected = claims.keys() - set(task.targets)
    if unexpected:
        problems.append(issue(Problem("UNREQUESTED_CLAIM", "Entwurf enthält ein Ziel außerhalb des Auftrags.")))
    facts = []
    for key in task.targets:
        claim = claims.get(key)
        if claim is None:
            problems.append(issue(Problem("MISSING_CLAIM", "Pflichtclaim fehlt."), key))
            continue
        try:
            support = verify_evidence(claim, vault)
            value, citations = check_consistency(claim, support, vault)
            require_supported_value(claim, value)
        except Problem as exc:
            problems.append(issue(exc, key))
            continue
        field = vault.fields[key.field]
        facts.append({
            **key._asdict(), "value": value, "unit": field.unit,
            "method": "sum" if field.sum_of else "direct",
            "citations": [vault.cite(record, role) for record, role in citations],
        })
    if problems:
        return {"status": "BLOCKED", "issues": problems}
    return {"status": "RELEASED", "answer": facts}


def decide(vault_path, task_path, draft_path):
    trace = {}
    phase = "preflight"
    try:
        task, vault = prepare(vault_path, task_path, trace)
        phase = "draft"
        claims = extract_claims(_input(draft_path, "draft", trace), vault.fields)
        phase = "claims"
        return _check_claims(task, vault, claims), trace
    except Problem as exc:
        status = "BLOCKED" if phase == "draft" and exc.code != "FILE_UNAVAILABLE" else "CHECK_ERROR"
        return {"status": status, "phase": phase, "issues": [issue(exc)]}, trace
    except Exception:
        problem = Problem("INTERNAL_ERROR", "Interner Prüffehler. Freigabe abgebrochen.")
        return {"status": "CHECK_ERROR", "phase": phase, "issues": [issue(problem)]}, trace
