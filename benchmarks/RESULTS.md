# Initial synthetic extraction benchmark

Run date: 2026-09-18. Provider: Groq. Six synthetic text cases, 39 labeled fields per model, one call per case, no retries. Temperature 0; max_tokens 3000; JSON object response format. Inputs and expected labels are in synthetic.json. Expected labels were not sent to models.

| Model | Correct / attempted fields | Failed requests | Median elapsed seconds (all attempts) | Reported prompt / completion tokens |
|---|---:|---:|---:|---:|
| openai/gpt-oss-20b | 37/39 | 0/6 | 1.305 | 2234 / 4890 |
| openai/gpt-oss-120b | 38/39 | 0/6 | 2.010 | 2234 / 3710 |
| qwen/qwen3.8-27b | 37/39 | 1/6 | 0.841 | 1520 / 1175 |

Qwen's multiple-document case received HTTP 429. Its two expected fields count as unsuccessful; this is a service availability outcome, not evidence of wrong extraction. Qwen returned 37/37 correct fields over its five successful calls. Its latency median includes the failed request and must not be interpreted as a clean speed comparison.

Both GPT-OSS models used an empty calendar where the provisional label expected `unknown`. The prompt allows empty unknown text, so this is a representation disagreement, not an invented date. GPT-OSS 20B also returned `Br` instead of requested ISO `ETB`; the application's normalization can map that alias, but this test scores raw model fields. No labeled unknown-field inventions were observed on successful responses; this metric does not evaluate every form of hallucination or evidence-quote accuracy.

Fixtures cover invoice total versus amount due, unknown fields, credit-note classification, an embedded instruction, an Ethiopian-calendar receipt, and multiple receipts. No scans, handwriting, OCR, multilingual extraction, repeated trials, or human correction time are measured. Labels are authored and provisional, not independently adjudicated. No production accuracy or model winner is established. Costs are not calculated because token reports alone are not proof of billed cost.

The existing default remains unchanged. Next: independently review labels, test a representative document set with fixed OCR outputs, repeat runs, and measure correction effort.

Prompt SHA-256: `4df812380f41ace25695ee15e4e286fa0947ae0a7b527410a7ed94ceeaaf17a7`

Fixture SHA-256 (sorted JSON serialization): `f1818873c6b00845b3b1e5989c1b465e640692282081236ec92082f9531fdf1b`
