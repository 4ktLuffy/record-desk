# Inventory investigation experiment

Reproduce with `python inventory_lab.py --seed 42 --output benchmark-results/inventory-42.json`. The UI runs seed42. This directory includes aggregate results for prespecified seeds42,73,109; the fixed thresholds were not tuned against their held-out labels.

Each seed produces 4,800 synthetic daily records:120days ×20products ×2warehouses. Days0–59 train historical profiles;60–89 calibrate the robust threshold;90–119 evaluate. Expected labels are returned separately from records and are used only for scoring. Training/calibration contain no planted anomalies; this simplifying assumption is optimistic. Test data includes legitimate promotions and sales spikes of differing sizes, plus deliberately inconsistent closing quantities. No returns, transfers, seasonality, missing records, supplier documents, or real organizations are simulated yet.

## Methods

- Arithmetic reconciliation checks closing = opening + received − sold. Its perfect synthetic result is expected because planted errors directly violate that equation; this does not measure detection of missing transactions or incorrect but internally consistent data.
- Global sales baseline: training mean +3standard deviations across all products/locations.
- Per-product/location robust method: (sales − training median)/max(1,1.4826×median absolute deviation). Threshold is max(3.5,99th percentile of unlabelled calibration scores). Both statistical methods are one-sided and target high sales only.

## Held-out results

| Seed | Global precision / recall | Robust precision / recall | Global / robust false alerts |
|---|---|---|---|
|42|75.8% /53.2%|62.3% /70.2%|8 /20|
|73|58.3% /30.4%|55.3% /56.5%|10 /21|
|109|71.4% /61.0%|59.3% /85.4%|10 /24|

The per-product method has higher recall and lower precision on all three seeds. It is not an unconditional improvement. Promotions and ordinary tail events create false alerts; subtle planted anomalies can be missed. A production choice requires review capacity, business costs, and representative real data. These are descriptive results, not confidence intervals or evidence of real-world performance.

## Traceability and limits

Every alert includes an immutable generator record ID, product/location/date, observed quantities, expected closing, historical median/scale, and promotion context. Review priority uses absolute discrepancy ×synthetic unit value, followed by anomaly score. This is an investigation priority heuristic, not proven loss or a calibrated probability. Explanations are generated directly from these numbers; no model is asked to invent causes.

The generator source and seed recreate the full dataset, whose SHA-256 is in every report. Reports preserve false positives and false negatives. The UI does not modify uploaded datasets or automatically correct stock. This is a separate research lab, not yet a production detector for arbitrary imports.

Next: independently review scenarios, test time/seasonal effects and contaminated training data, add negative anomalies, fit review-capacity thresholds using calibration only, and validate on a consenting organization's dataset. Do not tune against this held-out test set and continue calling it unseen.
