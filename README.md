# KYC Document Screener

A Python tool that pre-screens credit-card onboarding (KYC) application packets. It reads the PDF documents,
works out what each one is, extracts the key fields, and runs 14 completeness, consistency, privacy, financial-crime
and tampering checks. Each packet is routed to **CLEAR**, **REVIEW** or **ESCALATE**, and reviewers get a queue in
which every finding comes with its evidence.

Reviewers then spend their time on the packets that need judgement and stop re-reading clean files field by field.

## 1. What it checks

| Code | Check | Severity |
|---|---|---|
| `MISSING_ID_PROOF` / `MISSING_ADDRESS_PROOF` / `MISSING_INCOME_PROOF` | Required document categories are present (PAN; Aadhaar or utility bill; salary slip) | Medium |
| `UNREADABLE_DOC` | PDF has no text layer (for example, a scanned image) or is an unrecognised type, so it needs a manual look | Medium |
| `PAN_INVALID_FORMAT` | PAN on the application fails the individual-PAN format `AAAPA9999A` | Medium |
| `PAN_MISMATCH` | PAN on the card differs from the application | High |
| `NAME_MISMATCH` | The name on a document doesn't match the application. Case, punctuation, dropped middle names and initials are tolerated, but the first name and surname must agree | High |
| `DOB_MISMATCH` | Date of birth on PAN or Aadhaar differs from the application | High |
| `ADDRESS_PIN_MISMATCH` | PIN code on an address proof differs from the application | Medium |
| `STALE_ADDRESS_PROOF` | Utility bill older than 90 days at submission | Medium |
| `INCOME_MISMATCH` | Declared annual income is more than 1.4× the net pay on the salary slip × 12 | Medium |
| `UNMASKED_AADHAAR` | Full 12-digit Aadhaar number visible. This is a privacy breach, and the number is not reproduced in the findings | Medium |
| `WATCHLIST_HIT` | Fuzzy match (≥ 0.85) of the applicant's name against a sanctions/PEP list, robust to dropped middle names and one-letter spelling changes | High |
| `METADATA_TAMPER` | PDF produced by a known editing tool, or modified long after creation | High |

**Routing:** any High finding → ESCALATE; otherwise any Medium finding → REVIEW; no findings → CLEAR.
All thresholds, severities, the PAN pattern and the list of editing tools are in [`config/rules.json`](config/rules.json).

## 2. How it works

```
packet folder (application.json + doc_*.pdf)
   │
   ├─ extract.py   pypdf text + metadata ─► classify document by content (file names are ignored)
   │               ─► regex field extraction (name, DOB, PAN, masked Aadhaar, PIN, bill date, net pay)
   ├─ checks.py    14 rules ─► Findings (code, severity, message, evidence, document)
   ├─ screen.py    route each packet ─► reviewer queue sorted ESCALATE → REVIEW → CLEAR
   │               ─► Excel report, CSVs, accuracy against ground truth, review-time model
   └─ generate.py  synthetic packets as real PDFs with planted defects (for testing)
```

## 3. Results on 500 synthetic packets (1,760 PDFs, seed 42)

| | |
|---|---|
| Routed | 319 CLEAR · 125 REVIEW · 56 ESCALATE |
| Packets with planted defects | 181 |
| Routing accuracy vs ground truth | 100% (0 defective packets cleared, 0 clean packets sent to review) |
| Reason-code accuracy | Precision and recall of 1.00 on all 14 checks |
| Processing speed | ~30–65 ms per packet on a laptop |
| **Estimated review time** | **4,500 → 1,482 reviewer-minutes (−67%)** under the time model below |

**Read these numbers carefully.** The test packets come from the generator in this repo, and the rules were written
with the same document layouts in mind. 100% accuracy therefore shows that every rule is implemented correctly
end to end: PDF → text → fields → finding → routing. It does **not** predict accuracy on real scanned documents, which
would need OCR and messier extraction. The suite also includes benign name variations (upper case, initials, dropped
middle names) to show that the name rules don't over-flag.

**Time model.** These are assumptions, set in `rules.json`, to be replaced with timings from a pilot:

| Route | Manual review (min/packet) | With tool (min/packet) |
|---|---:|---:|
| CLEAR | 9.0 | 1.5 (spot-check the summary) |
| REVIEW | 9.0 | 4.0 (findings pre-filled, check flagged items only) |
| ESCALATE | 9.0 | 9.0 (full review) |

The saving depends on the mix of packets. The more packets are clean, the bigger the saving.

## 4. Outputs (`reports/`)

- `screening_report.xlsx` – Summary, Reviewer Queue (colour-coded routes), Findings with evidence, Accuracy by Check, Time Model
- `reviewer_queue.csv` – one row per packet: route, counts by severity, reason codes, evidence summary
- `findings.csv` – one row per finding
- `accuracy_by_check.csv` – planted vs flagged, TP/FP/FN, precision, recall per check
- `summary.json` – routing counts, packet-level accuracy and confusion matrix, time model, processing speed

## 5. How to run

```bash
pip install -r requirements.txt
python -m kycscreen.screen                         # generate 500 packets, screen them, score against ground truth
python -m kycscreen.screen --n 2000 --seed 7       # bigger or different set
python -m kycscreen.screen --packets samples       # screen the 5 sample packets committed in samples/
python -m pytest -q                                # 12 tests: name matching, watchlist, each defect, routing, PDF round trip
```

To screen your own packets, put each one in its own folder with an `application.json` (see `samples/*/application.json`)
and the PDFs, then pass the parent folder to `--packets`.

## 6. Design choices

- **Evidence-first findings.** Every finding states what was compared, such as *"bill dated 22-Mar-2026, 176 days before
  submission"*, so the reviewer can confirm it without reopening the file.
- **Privacy by design.** An unmasked Aadhaar number is flagged but never copied into any report.
- **Unreadable before missing.** If a document can't be read, the tool raises `UNREADABLE_DOC` rather than also
  claiming the document category is missing.
- **Configurable rules.** Thresholds and severities live in config, not code, so policy changes don't need a release.

## 7. Limitations

- Text-layer PDFs only. Scanned images are routed to manual review instead of being OCR'd.
- The extraction patterns are written for the synthetic layouts. Real issuers' layouts would need their own patterns or a layout model.
- The watchlist and all applicant data are **fictional**.
