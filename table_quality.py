"""Deterministic, non-mutating quality checks for business CSV files."""
import csv
import io


def inspect_csv(content):
    if not isinstance(content, str) or len(content.encode('utf-8')) > 2_000_000:
        raise ValueError('Use a UTF-8 CSV file up to 2 MB.')
    if '\x00' in content:
        raise ValueError('Binary content is not supported. Export UTF-8 CSV.')
    try:
        rows = list(csv.reader(io.StringIO(content.lstrip('\ufeff'), newline=''), strict=True))
    except csv.Error as exc:
        raise ValueError('Invalid CSV: ' + str(exc)) from exc
    if not rows or not rows[0]:
        raise ValueError('CSV must contain a header row.')
    if len(rows) > 10001 or len(rows[0]) > 100:
        raise ValueError('Limit: 10,000 data records and 100 columns.')
    headers, data = rows[0], rows[1:]
    issues = []
    counts = {}

    def issue(kind, record, column, message):
        counts[kind] = counts.get(kind, 0) + 1
        if len(issues) < 200:
            issues.append(dict(kind=kind, record=record, column=column, message=message))

    seen_headers = set()
    for index, header in enumerate(headers, 1):
        key = header.strip().casefold()
        if not key or key in seen_headers:
            issue('header', 1, index, 'Blank or repeated column name; assign a unique name before mapping.')
        seen_headers.add(key)
    seen = {}
    missing = [0] * len(headers)
    for number, row in enumerate(data, 2):
        if len(row) != len(headers):
            issue('row_width', number, None, f'Expected {len(headers)} cells; found {len(row)}.')
        identity = tuple(row)
        if identity in seen:
            issue('duplicate', number, None, f'Exact same values as record {seen[identity]}; review before removing.')
        else:
            seen[identity] = number
        for index in range(len(headers)):
            value = row[index] if index < len(row) else ''
            if not value.strip():
                missing[index] += 1
                issue('missing', number, index + 1, 'Empty cell; whether it is required depends on your business rules.')
            elif value != value.strip():
                issue('whitespace', number, index + 1, 'Leading or trailing whitespace; source value preserved.')
    return dict(records=len(data), columns=[dict(position=i+1, name=h, missing=missing[i]) for i,h in enumerate(headers)],
                counts=counts, issues=issues, total_issues=sum(counts.values()),
                note='Record 1 is the header; quoted multiline cells count as one record. First 200 findings shown. No values changed or sent to AI. These checks do not establish business accuracy or completeness.')
