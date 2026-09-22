# Reconcile your own snapshots

Use **Reconcile snapshots** to compare two real CSV exports, nominate a reference, inspect exact differences, and create a separate candidate. Customer records, orders, inventories and other tabular datasets use the same workflow. No provider call is involved.

## Try the example

1. Start `python3 server.py` and open http://127.0.0.1:8765/#recon-workflow.
2. Upload and save [reference.csv](../examples/reconciliation/reference.csv) as the reference and [delivery.csv](../examples/reconciliation/delivery.csv) as the delivery.
3. Load identity columns and select **id** and **region** together. A customer ID may occur in different regions; the pair identifies a record.
4. Enter **2026-09-21** for both snapshot dates, **2026-09-22** as the assessment date, and **1** as maximum age. These fixed dates make the example reproducible; use the actual source dates for your own data.
5. Declare the authority as “Synthetic owner-approved reference” and the scope as “All accounts in the example.” Confirm the complete-snapshot assumption.
6. Compare. You should see five discrepant identities: one changed, one duplicate, two missing and one unexpected. Column order differs intentionally and is handled by name.
7. Select the unexpected identity **["X", "South"]**. The label explicitly says its delivered rows will be removed. Enter a rationale and build a candidate. Both extra occurrences disappear from that candidate; four discrepancies remain.
8. Reopen the original, select all five discrepancies and explain the correction. The new candidate matches the nominated reference. Originals remain unchanged. Download its CSV or the full evidence bundle.

Try assessment date **2026-09-23** without changing the age limit. The files are stale under that declared rule, so candidate repairs are blocked even if all values match.

## Contract and checks

- Both files must be UTF-8 CSV, at most 2 MB, 10,000 data records and 100 columns. Headers must be unique and nonblank; row widths must be correct. Structural errors reject the comparison, preserving the saved inputs.
- Column names must match exactly on both sides. Column order may differ. Renaming, type inference, unit conversion and field mapping are outside this contract.
- Choose one or more **identity columns**. Keys use exact, case-sensitive tuples, so embedded separators cannot cause collisions. Blank or padded identity values are retained as invalid record evidence and block candidate repair; they are never trimmed silently.
- All other fields are compared as exact text. `1` and `1.00` differ. Spaces and quoted multiline cells are preserved. Agreement is not a type, accounting or business-rule validation.
- A reference must be nonempty and have unique valid identities. Ambiguous references block all candidate repair; the system does not choose a winner.
- The user declares the reference authority and complete population scope. Both declared snapshot dates must match. Neither can be in the future relative to the assessment date; age must be at most the declared maximum. All dates are Gregorian calendar dates.
- Completeness and authority are declarations, **not independently verified guarantees**. The tool does not inspect export manifests, validate a source-system signature, infer dates from row values, or prove that the nominated reference is correct.
- Freshness is pinned to the recorded assessment date. Reopening a saved report does not reevaluate it against today's clock. Run a new comparison with a new assessment date to reassess freshness.
- An empty delivery is valid input for comparison: every reference identity is missing. An empty reference is blocked. Unexpected delivered identities are discrepancies even if their rows look valid.

## Candidate semantics

Repair applies only to explicitly selected discrepancies and requires a rationale. A selected missing, changed or duplicate identity is replaced with its **one exact reference row**. An unexpected identity has no reference row, so selecting it explicitly removes **all delivered occurrences** from the candidate. This is not source deletion.

Unselected rows retain their order and values. Selected replacements are appended in deterministic issue-ID order, using the delivery's original column order. Record numbers in the transformation log refer to original inputs; candidate evidence uses candidate record positions. Header/CSV serialization can differ; preserved field values are the invariant.

The original comparison remains immutable. Each candidate starts from that original; candidate chaining is rejected. Partial repairs remain discrepant, and stale/ambiguous/invalid-key blockers prevent repair altogether. A matching candidate is **not automatically published**, and matching a chosen reference is not universal certification that the data is correct.

The UI paginates discrepancies in groups of 50, retaining selection and the rationale across pages. “Select discrepancies on this page” does exactly that. Full reports and bundles include every discrepancy and all invalid records.

## Evidence and persistence

`reconciliation.py` uses the existing SHA-256 dataset store. Engine version, contract, both source fingerprints, row evidence, candidate parent, transformation log and rationale determine a stable report ID. Repeated/concurrent identical requests reuse the persisted report. SQLite triggers reject normal updates/deletes; the local database owner can still alter the schema.

Saved history lists the latest 50 reports; older reports remain retrievable by ID. Evidence bundles contain raw CSV text and hashes, report and parent report. CSV downloads use the existing spreadsheet-formula protection; JSON evidence preserves exact original text, including formula-like cells. Bundles can contain your actual business data: they remain local unless you share them.

Local API:

- `POST /api/reconciliation-compare`: `reference_id`, `delivery_id`, `keys` (array), `authority`, `scope`, `reference_date`, `delivery_date`, `as_of`, `max_age_days` (integer, 0–3650), `confirmed: true`.
- `POST /api/reconciliation-repair`: `run_id`, `issue_ids` (array of IDs from that report), `note`.
- `GET /api/reconciliation/<id>`, `/api/reconciliation-bundle/<id>`, `/api/reconciliations`.

Requests use the existing same-origin/local-host restrictions. These endpoints do not add hosted authentication, tenant isolation, scheduling or external integrations.

## Verification

Tests cover composite identities, reordered headers, every discrepancy class, explicit removal of unexpected duplicates, partial/full candidates, empty files, stale/future/mismatched dates, invalid identities, ambiguous references, exact numeric spelling, embedded separators, multiline/formula-like cells, source hashes, concurrent replay, immutable history, larger issue sets and the complete HTTP workflow.

The synthetic revenue replay remains unchanged. Its fixed scenario metrics are separate from this generic exact-text reconciliation workflow; this feature does not infer revenue from arbitrary columns.
