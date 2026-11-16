# Anti-Hallucination Gate

Anti-Hallucination Gate is an offline command-line tool for checking structured factual claims against local evidence. It releases an answer only when every required claim passes verification and the audit record has been written. Releases include the cited snapshot, its SHA-256 hash, the exact evidence line, and its reference to the original material.

## Quick start

Use Python 3.9 or later. The tool uses only the standard library. From the repository root, run the fictional warehouse example.

```sh
python3 -B -m alignment_gate check \
  --vault demo/fiction/vault.json \
  --task demo/fiction/tasks/standard.json \
  --draft demo/fiction/drafts/belegt.json \
  --audit-dir work/audit
```

The example checks the inventory date and warehouse status, recalculates the total as 12 + 8, and compares it with the reported total. To see a blocked answer, use `demo/fiction/drafts/erfundene-quelle.json` as the draft. It cites an unknown source.

| Exit code from `check` | Status | Meaning |
| --- | --- | --- |
| 0 | `RELEASED` | All required checks passed and the audit was saved. `answer` contains the released facts. |
| 1 | `BLOCKED` | A claim or the draft failed verification. The output contains issue codes. |
| 2 | `CHECK_ERROR` | A required input, check, or audit write failed. The output contains issue codes. |

A downstream consumer should require exit code 0, complete JSON, and `status == "RELEASED"` before using `answer`. The example data and human-readable diagnostics are in German. The JSON keys, status values, and issue codes are language-independent identifiers.

## How it works

| Module | Responsibility |
| --- | --- |
| Prompt Compiler | Preserves the original request, checks the declared scope and source constraints, and returns a structured draft contract. |
| Evidence Vault | Loads allowed JSONL snapshots, checks their pinned hashes, and indexes exact evidence lines. |
| Claim Extractor | Reads atomic claims from a strict JSON schema and rejects duplicate, missing, or unexpected fields. |
| Evidence Verifier | Resolves citations and checks the subject, field, date, and typed value. |
| Consistency Engine | Checks all allowed sources for conflicts at the same date. Recalculates declared integer sums and cross-checks any stated totals. |
| Fail-Closed Release Gate | Requires exactly one verified claim per requested target and constructs the release from checked values. |
| Audit and Evaluation | Records the decision and input hashes locally. Evaluates cases against separate reference decisions and values. |

Use the compiler to inspect a draft contract before checking an answer.

```sh
python3 -B -m alignment_gate compile \
  --vault demo/fiction/vault.json --task demo/fiction/tasks/standard.json
```

## Use your own evidence

1. Prepare a trusted directory containing a vault manifest and `sources/<source-id>.jsonl` files. Each source line contains `subject`, `field`, `as_of`, `value`, and `origin_ref`. Use UTF-8 and record an exact locator in the original material. Use a directory outside the checkout for private inputs.
2. Review the sources and pin the SHA-256 of each snapshot in the manifest. Define the field types as `integer`, `date`, or `text`. Integers require a unit. A calculated total uses `sum_of` with at least two distinct direct integer fields in the same unit.
3. Define a task with `schema: 1`, `request`, `allowed_sources`, and `targets`. Each target is a subject, field, and `as_of` date in `YYYY-MM-DD` format. Use `excluded_sources` to record explicitly excluded sources when needed.
4. Submit a draft with `schema: 1` and `claims`. Each claim adds a unique `id`, typed `value`, and `evidence` entries with a source ID and a one-based line number to the target. Give a separate citation for each operand of a sum.
5. Run `check` with your vault, task, draft, and audit directory. Use the returned issue codes and evidence locations to investigate blocked answers.

See [the example vault](demo/fiction/vault.json), [task](demo/fiction/tasks/standard.json), and [draft](demo/fiction/drafts/belegt.json) for complete configurations. The bundled examples and evaluation cases are fictional.

## Test and contribute

```sh
python3 -B -m unittest discover -s tests -v
python3 -B -m alignment_gate evaluate --suite demo/suite.json --audit-dir work/audit
```

The evaluation compares decisions and released values with independent fixture labels. Answer rate, unexpected releases, unexpected blocks, check errors, and claims outside the reference are reported separately. These are results for a small regression suite.

When reporting a bug, include the tool version, Python version, issue code, and a reproducible example with fictional data. For a change to the release rules, include a passing example and a case the rule must block. Run the tests and the evaluation before opening a pull request.

## Scope and trust

- The operator controls the task, the pinned manifest and sources, the audit directory, and the evaluator's expected answers. Keep those inputs under trusted local access controls.
- Version 0.1 checks structured claims. The operator translates free-form requests into complete targets and reviews the source material. A matching hash proves snapshot integrity against the manifest. Claim verification remains limited to the reviewed snapshot, its recorded scope, and the configured rules.
- Arithmetic is limited to declared integer sums with matching units. Text values use exact matching.
- The gate controls its own structured output. Any model-generated prose or later transformation that reaches the user requires its own verification path.
- Audit files are written atomically to local storage. A user with write access can alter them. Keep the corresponding input versions for later review.
- A wrong or stale source may still support a claim under these checks. The regression suite cannot establish a real-world hallucination rate.

## License

This project is available under the [MIT License](LICENSE).
