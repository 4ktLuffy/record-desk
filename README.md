# Record Desk

[![Checks](https://github.com/4ktLuffy/record-desk/actions/workflows/checks.yml/badge.svg)](https://github.com/4ktLuffy/record-desk/actions/workflows/checks.yml)

Turn messy business files into checked, traceable records.

A local data-quality and reconciliation workspace. Compare spreadsheet exports, inspect discrepancies, preview cleanup, and preserve a trail back to the original values. No account or credentials are needed for the CSV workflow.

## Quick start

Requires Python 3.9 or newer. No pip packages or frontend build required.

```sh
git clone https://github.com/4ktLuffy/record-desk.git
cd record-desk
python3 server.py
```

On Windows use `python server.py`. Open http://127.0.0.1:8765. To choose a different port: `python3 server.py --port 8766`.

Click **Try inventory demo**, then **Compare datasets**. The two synthetic exports deliberately contain a quantity conflict, duplicate product keys, a spaced product code, and products present on only one side. No private data is included.

1. Inspect the summary and filter duplicate keys or conflicts.
2. Choose **Preview left-side whitespace cleanup**.
3. Review the original and proposed values; apply to create a new dataset.
4. Select its key/value columns and compare again. The spaced product code now matches; the duplicate and quantity conflict remain visible.
5. Download the complete JSON report, including dataset fingerprints, mapping, source record numbers, and every result group.

## Capabilities

- CSV structural checks: missing cells, repeated rows, ambiguous headers, row lengths, surrounding whitespace.
- Persistent datasets identified by SHA-256; identical imports reuse content.
- Explicit key/value column selection for comparing any two well-formed CSVs.
- Matched, conflicting, one-sided, invalid, and ambiguous key groups. Duplicate keys never silently disappear.
- Reviewable whitespace trimming into a separate version with source linkage and audit history.
- Result filtering and complete report export, independent of the first-200-group display limit.
- Download saved/cleaned CSVs from the saved-dataset panel. Formula-like cells receive a leading apostrophe for spreadsheet safety; stored originals remain exact.
- Optional receipt/invoice extraction, human review, approved-record totals, and comparison against mapped CSVs.

## Comparison semantics

Generic comparison uses exact, case-sensitive text. `1` and `1.0` differ. Units, locale, currencies, and dates are not guessed. Blank keys/values are invalid and repeated keys are ambiguous. A one-sided record is absent from the other uploaded dataset; it is not proof that a transaction or document does not exist. Counts represent key groups rather than individual rows.

CSV input is UTF-8 and comma-separated, with up to 2 MB, 10,000 data records, and 100 header columns. Excel files must be exported as CSV. Corrections to duplicate headers or malformed rows must be made in the source and reimported. Cleanup currently supports surrounding whitespace only, not deduplication or type conversion.

## Optional document processing

The CSV workflow runs locally without a provider. Receipt extraction and natural-language questions use Groq's OpenAI-compatible API and require `GROQ_API_KEY` in the process environment. `EVAL_MODEL_NAME` optionally selects a model; the default is `openai/gpt-oss-20b`. Account limits and provider charges may apply. Extraction sends document text to Groq. Questions send the question and planning instructions. Provider-generated fields require human review before use in totals; extraction can be incorrect.

OCR and PDF splitting currently require macOS and Swift developer tools. On macOS, `./start.sh` builds the OCR helper before starting. CSV features and manual document review do not require Swift. Portable OCR is future work. Set `RECORD_DESK_IMPORT` for optional local folder import; uploading individual supported files also works. `.env` files are not automatically loaded.

## Privacy and scope

Single-user localhost application; not a hosted multi-user service. Do not expose this server to the internet. State is stored outside the repository at `~/.local/share/record-desk`, or the absolute directory specified by `RECEIPT_DESK_STATE`. That directory contains private originals/derived files and the SQLite database. Do not commit it. There is no telemetry or remote processing for CSVs. Original imported document files remain in their existing location.

## Architecture

```text
CSV → structural checks → immutable source dataset
                           ↓ explicit mapping
                      comparison engine → issue groups → full report
                           ↓ reviewed cleanup
                      derived dataset + source-linked history

Documents → local text extraction → optional provider → review → approved records
```

`table_quality.py` inspects structure, `datasets.py` handles persistence/cleanup/comparison, `core.py` owns document processing, and `server.py` exposes same-origin local endpoints. SQLite stores records and audit events; the browser uses plain JavaScript.

## Development

```sh
python3 -m unittest discover -s tests -v
node --check dist/app.js
```

Tests use isolated temporary directories and synthetic inputs; they require no API key. CI is configured for Linux, macOS, and Windows on Python 3.9 and 3.12. Local passing tests are not evidence that every hosted CI job has passed.

## Roadmap

Configurable field rules, service-work reconciliation, approved type conversions, CSV result export, portable OCR, and accessibility testing. Authentication and tenant isolation are prerequisites for a hosted edition. See CONTRIBUTING.md and SECURITY.md.

## License

MIT. See [LICENSE](LICENSE).
