import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
DEMO = APP / "demo"


def load(path):
    return json.loads(path.read_bytes())


def write(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.demo = self.root / "demo"
        shutil.copytree(DEMO, self.demo)
        self.fiction = self.demo / "fiction"
        self.audit = self.root / "audit"
        self.vault = self.fiction / "vault.json"
        self.task = self.fiction / "tasks/standard.json"
        self.draft = self.fiction / "drafts/belegt.json"

    def cli(self, *args):
        process = subprocess.run(
            [sys.executable, "-B", "-m", "alignment_gate", *map(str, args)],
            cwd=APP, capture_output=True, encoding="utf-8", timeout=10,
        )
        self.assertEqual(process.stderr, "")
        return process.returncode, json.loads(process.stdout)

    def check(self, *, vault=None, task=None, draft=None, audit=None):
        return self.cli("check", "--vault", vault or self.vault, "--task", task or self.task,
                        "--draft", draft or self.draft, "--audit-dir", audit or self.audit)

    def assert_held(self, result, code, status="BLOCKED"):
        exit_code, decision = result
        self.assertEqual(exit_code, 1 if status == "BLOCKED" else 2, decision)
        self.assertEqual(decision["status"], status)
        self.assertNotIn("answer", decision)
        self.assertIn(code, [issue["code"] for issue in decision["issues"]])
        if "audit_id" in decision:
            event = load(self.audit / (decision["audit_id"] + ".json"))
            self.assertEqual(event["status"], status)
            self.assertNotIn("answer_sha256", event)
        return decision

    def set_manifest_hash(self, source, raw):
        data = load(self.vault)
        row = next(row for row in data["sources"] if row["id"] == source)
        row["sha256"] = hashlib.sha256(raw).hexdigest()
        write(self.vault, data)
        (self.fiction / "sources" / f"{source}.jsonl").write_bytes(raw)

    def test_releases_only_structured_values_and_exact_pinned_citations(self):
        code, decision = self.check()
        self.assertEqual(code, 0, decision)
        self.assertEqual(decision["status"], "RELEASED")
        answer = decision["answer"]
        self.assertEqual(
            [(f["field"], f["value"], f["method"], f["unit"]) for f in answer],
            [("gesamt", 20, "sum", "Stück"), ("status", "geschlossen", "direct", None),
             ("inventurtag", "2026-01-31", "direct", None)],
        )
        self.assertEqual([c["role"] for c in answer[0]["citations"]], ["operand", "operand", "cross_check"])
        for fact in answer:
            for citation in fact["citations"]:
                raw = (self.fiction / citation["snapshot"]).read_bytes()
                self.assertEqual(citation["sha256"], hashlib.sha256(raw).hexdigest())
                line = raw.decode("utf-8").splitlines()[citation["line"] - 1]
                self.assertEqual(citation["exact_line"], line)
        event = load(self.audit / (decision["audit_id"] + ".json"))
        canonical = json.dumps(answer, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        self.assertEqual(event["answer_sha256"], hashlib.sha256(canonical.encode("ascii")).hexdigest())
        self.assertEqual(event["inputs_sha256"]["draft"], hashlib.sha256(self.draft.read_bytes()).hexdigest())
        self.assertEqual(set(event["inputs_sha256"]["verified_sources"]), {"bestand", "summe"})

    def test_compile_retains_original_and_offers_only_matching_candidates(self):
        code, data = self.cli("compile", "--vault", self.vault, "--task", self.task)
        self.assertEqual(code, 0, data)
        self.assertEqual(data["status"], "DRAFT_CONTRACT")
        self.assertEqual(data["request"], load(self.task)["request"])
        self.assertNotIn("answer", data)
        self.assertEqual(
            [(c["source"], c["line"]) for c in data["targets"][1]["evidence_candidates"]],
            [("bestand", 3)],
        )

    def test_a_missing_claim_suppresses_all_answer_content(self):
        data = load(self.draft)
        data["claims"].pop()
        write(self.draft, data)
        self.assert_held(self.check(), "MISSING_CLAIM")

    def test_extra_claim_fails_even_if_fully_supported(self):
        data = load(self.draft)
        extra = dict(data["claims"][0], id="extra", field="typ-a", value=12,
                     evidence=[{"source": "bestand", "line": 1}])
        data["claims"].append(extra)
        write(self.draft, data)
        self.assert_held(self.check(), "UNREQUESTED_CLAIM")

    def test_free_prose_and_forged_verdict_are_rejected(self):
        data = load(self.draft)
        data["answer"] = "Diese ungeprüfte Behauptung darf nicht heraus."
        data["status"] = "RELEASED"
        write(self.draft, data)
        held = self.assert_held(self.check(), "INVALID_SCHEMA")
        self.assertNotIn(data["answer"], json.dumps(held))

    def test_duplicate_json_keys_are_rejected(self):
        data = self.draft.read_text(encoding="utf-8")
        self.draft.write_text(data.replace('"schema": 1', '"schema": 1, "schema": 1'), encoding="utf-8")
        self.assert_held(self.check(), "INVALID_JSON")

    def test_duplicate_claim_id_is_rejected(self):
        data = load(self.draft)
        data["claims"][1]["id"] = data["claims"][0]["id"]
        write(self.draft, data)
        self.assert_held(self.check(), "DUPLICATE_CLAIM")

    def test_nonexistent_line_is_not_support(self):
        data = load(self.draft)
        data["claims"][1]["evidence"][0]["line"] = 999
        write(self.draft, data)
        self.assert_held(self.check(), "UNKNOWN_CITATION")

    def test_number_typed_as_boolean_is_rejected_at_ref_boundary(self):
        data = load(self.draft)
        data["claims"][0]["evidence"][0]["line"] = True
        write(self.draft, data)
        self.assert_held(self.check(), "INVALID_SCHEMA")

    def test_paths_are_rejected_as_source_ids(self):
        data = load(self.draft)
        data["claims"][1]["evidence"][0]["source"] = "../../bestand"
        write(self.draft, data)
        self.assert_held(self.check(), "INVALID_SCHEMA")

    def test_source_exists_but_is_outside_allowlist(self):
        data = load(self.draft)
        data["claims"][0]["evidence"][0] = {"source": "widerspruch", "line": 1}
        write(self.draft, data)
        self.assert_held(self.check(), "UNKNOWN_CITATION")

    def test_valid_but_uncited_conflicting_source_blocks(self):
        decision = self.assert_held(self.check(task=self.fiction / "tasks/widerspruch.json"), "SOURCE_CONFLICT")
        conflict = decision["issues"][0]
        self.assertEqual(conflict["references"], [{"source": "bestand", "line": 1},
                                                   {"source": "widerspruch", "line": 1}])

    def test_sum_requires_both_operand_references(self):
        data = load(self.draft)
        data["claims"][0]["evidence"].pop()
        write(self.draft, data)
        self.assert_held(self.check(), "MISSING_CITATION")

    def test_total_line_cannot_replace_operands(self):
        data = load(self.draft)
        data["claims"][0]["evidence"] = [{"source": "summe", "line": 1}]
        write(self.draft, data)
        self.assert_held(self.check(), "IRRELEVANT_CITATION")

    def test_invalid_captured_date_and_mixed_units_block_policy(self):
        data = load(self.vault)
        data["fields"][0]["unit"] = "Euro"
        write(self.vault, data)
        self.assert_held(self.check(), "INVALID_POLICY", "CHECK_ERROR")
        data["fields"][0]["unit"] = "Stück"
        data["sources"][0]["captured_on"] = "2026-02-30"
        write(self.vault, data)
        self.assert_held(self.check(), "INVALID_SCHEMA", "CHECK_ERROR")

    def test_duplicate_task_target_is_never_silently_removed(self):
        data = load(self.task)
        data["targets"].append(data["targets"][0])
        write(self.task, data)
        self.assert_held(self.check(), "DUPLICATE_TARGET", "CHECK_ERROR")

    def test_compiler_rejects_conflicting_source_instructions(self):
        data = load(self.task)
        data["excluded_sources"] = ["bestand"]
        write(self.task, data)
        code, result = self.cli("compile", "--vault", self.vault, "--task", self.task)
        self.assertEqual(code, 2, result)
        self.assertEqual(result["issues"][0]["code"], "CONFLICTING_SCOPE")
        self.assert_held(self.check(), "CONFLICTING_SCOPE", "CHECK_ERROR")

    def test_pinned_but_invalid_trailing_line_blocks_entire_snapshot(self):
        path = self.fiction / "sources/bestand.jsonl"
        self.set_manifest_hash("bestand", path.read_bytes() + b'{"bad":true}\n')
        self.assert_held(self.check(), "INVALID_SOURCE", "CHECK_ERROR")

    def test_pinned_crlf_sources_retain_exact_line_and_correct_number(self):
        path = self.fiction / "sources/bestand.jsonl"
        raw = path.read_bytes().replace(b"\n", b"\r\n")
        self.set_manifest_hash("bestand", raw)
        code, decision = self.check()
        self.assertEqual(code, 0, decision)
        citation = decision["answer"][1]["citations"][0]
        self.assertEqual(citation["line"], 3)
        self.assertEqual(citation["exact_line"], raw.decode("utf-8").split("\r\n")[2])
        self.assertEqual(citation["sha256"], hashlib.sha256(raw).hexdigest())

    def test_audit_failure_suppresses_verified_answer(self):
        self.audit.write_text("not a directory", encoding="utf-8")
        decision = self.assert_held(self.check(), "AUDIT_WRITE_ERROR", "CHECK_ERROR")
        self.assertNotIn("audit_id", decision)

    def test_concurrent_runs_preserve_each_audit(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.check(), range(6)))
        self.assertTrue(all(code == 0 for code, _ in results), results)
        ids = {result["audit_id"] for _, result in results}
        self.assertEqual(len(ids), 6)
        self.assertEqual({file.stem for file in self.audit.iterdir()}, ids)
        self.assertTrue(all(load(path)["status"] == "RELEASED" for path in self.audit.iterdir()))

    def test_evaluation_runs_all_cases_against_separate_labels(self):
        code, report = self.cli("evaluate", "--suite", self.demo / "suite.json", "--audit-dir", self.audit)
        self.assertEqual(code, 0, report)
        metrics = report["metrics"]
        self.assertEqual(metrics["matched_cases"], {"count": 15, "total": 15, "rate": 1.0})
        self.assertEqual(metrics["false_releases"], {"count": 0, "total": 13, "rate": 0.0})
        self.assertEqual(metrics["released_claims_outside_reference"], {"count": 0, "total": 4, "rate": 0.0})
        self.assertEqual(len(list(self.audit.glob("*.json"))), 15)

    def test_evaluation_detects_wrong_answer_with_correct_release_status(self):
        path = self.demo / "suite.json"
        suite = load(path)
        suite["cases"][0]["expected_answer"][0]["value"] = 21
        write(path, suite)
        code, report = self.cli("evaluate", "--suite", path, "--audit-dir", self.audit)
        self.assertEqual(code, 1, report)
        self.assertEqual(report["metrics"]["released_claims_outside_reference"]["count"], 1)
        self.assertEqual(report["cases"][0]["actual"], "RELEASED")
        self.assertFalse(report["cases"][0]["matched"])

    def test_historical_snapshot_matches_only_its_own_date(self):
        case = "historischer-stichtag.json"
        code, decision = self.check(task=self.fiction / "tasks" / case,
                                    draft=self.fiction / "drafts" / case)
        self.assertEqual(code, 0, decision)
        self.assertEqual(len(decision["answer"]), 1)
        fact = decision["answer"][0]
        self.assertEqual((fact["as_of"], fact["value"]), ("2025-12-31", "offen"))
        self.assertEqual(fact["citations"][0]["line"], 5)


if __name__ == "__main__":
    unittest.main()
