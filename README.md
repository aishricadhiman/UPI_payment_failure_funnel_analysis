# UPI Payment Funnel & Failure Analytics

End-to-end analysis of UPI transaction failures across a 7-stage payment funnel — identifying exactly where, why, and for which segments transactions break down, using a multi-table relational dataset, MySQL for data engineering, and Python for analysis.

---

## Project Objective

Aggregate UPI success-rate dashboards (typically reported as a single ~95-97% figure) hide where the actual problem lies. This project answers the question a payments company's leadership would actually ask:

> *At which exact stage of the funnel are transactions failing, for which banks/devices/segments, and what should be fixed first?*

**Scope of this analysis:**
- 197,600 unique transactions across a 7-stage UPI payment funnel
- Stage-wise drop-off quantification (where failures happen)
- Failure categorization aligned to NPCI's real-world taxonomy: Business Decline / Technical Decline / Deemed Approved
- Segment-wise breakdown by bank tier, individual bank, device OS, network type, and transaction type
- Root-cause mapping using granular error codes, not just pass/fail flags

---

## Tech Stack

| Layer | Tool | Purpose |
|---|---|---|
| Data modeling & storage | **MySQL** | Relational schema across 7 linked tables (transactions, stage events, banks, devices, funnel stages, error codes, daily summaries) |
| Data engineering | **SQL** (via SQLAlchemy) | Table creation, multi-table joins, deduplication, null handling, and building the analysis-ready `transaction_summary` table |
| Analysis | **Python** (pandas, NumPy, Matplotlib/Seaborn) | Funnel conversion calculations, segment-wise failure rate analysis, statistical comparisons |
| Dashboard | **Power BI** | Stakeholder-facing interactive view of funnel drop-off and segment failure rates *(in progress)* |

---

## How the Data Was Built and Processed

This project intentionally mirrors how transaction data actually sits in a payments company's warehouse — **not** as one clean pre-joined file, but as separate raw tables that have to be modeled, cleaned, and joined before any analysis is possible.

**1. Database design (MySQL)**
Designed and created a 7-table relational schema:
- `transactions` — one row per transaction (payer/payee, amount, final outcome)
- `stage_events` — one row per transaction *per funnel stage reached* (a transaction that fails at Stage 3 has 3 rows, not 7 — it never reaches the remaining stages)
- `banks`, `devices`, `funnel_stages`, `error_codes` — reference/lookup tables
- `daily_bank_summary` — an independently-aggregated ops-side table used purely for cross-validation

**2. Data cleaning (SQL + Python)**
Ran a structured data-quality audit across all 7 tables and resolved:
- Duplicate `transaction_id` records (logging retry glitch)
- Duplicate bank entries under different IDs with inconsistent name casing (simulating a merge of two source systems)
- Missing `device_id` / `payee_bank_id` values on a subset of transactions
- ~230 `FAILED` stage events with a missing `error_code` — flagged explicitly as `UNKNOWN` rather than imputed, since multiple error codes can map to the same stage and guessing a specific cause would fabricate a fact not present in the data
- 120 orphaned `stage_events` rows referencing non-existent transactions, removed via inner-join logic

**3. Building the analysis layer (SQL)**
Joined all 7 tables into a single `transaction_summary` table at the transaction-stage grain, using `INNER JOIN` against `transactions` (correctly dropping orphaned events) and `LEFT JOIN` against all dimension tables (correctly preserving rows even when device/bank/error-code info wasn't logged). Banks were joined **twice** under separate aliases to capture both payer-side and payee-side bank tier independently.

**4. Reconciliation check**
Cross-validated aggregated counts from `transaction_summary` against the independently-reported `daily_bank_summary` table — a deliberate check for whether two "sources of truth" that should agree actually do, which surfaced expected small discrepancies from rounding and reporting-window differences.

**5. Analysis (Python)**
With clean, joined data in place, used pandas to calculate stage-to-stage funnel conversion rates, failure-category splits, and segment-wise (bank tier, device, network, transaction type) failure rate comparisons.

---

## Transaction Funnel Performance

![Transactions stopped by stage](figures/transactions-stopped-by-stage.png)

- Completed through **STG7** (final settlement): **186,232 transactions** — **94.25%** completion rate
- Dropped before completion: **11,368 transactions** — **5.75%** drop-off rate
- **Biggest drop-off stage: STG3 — UPI PIN Authentication**
  - 3,554 stopped transactions — **31.26%** of all drop-offs
- **Second biggest drop-off stage: STG5 — Issuing Bank Processing**
  - 2,473 stopped transactions — **21.75%** of all drop-offs
- **STG3 + STG5 combined account for 53.01% of all failures** — meaning more than half the problem is concentrated in just two of seven stages

---

## Failure Categories

![Failure by error category](figures/failure-by-error-category.png)

| Category | Failures | % of all failures |
|---|---|---|
| Business Decline (user-side) | 5,456 | 47.99% |
| Technical Decline (infra-side) | 4,173 | 36.71% |
| Deemed Approved (debited, not credited) | 1,511 | 13.29% |
| Unmapped / unknown cause | 228 | 2.01% |

Business Decline is the largest category by volume, but **Deemed Approved — while the smallest bucket — is the highest-severity category**, since it represents real customer money in limbo and is the leading driver of support escalations and trust erosion.

---

## Top Failure Reasons

![UPI failure reasons](figures/upi-failure-reasons.png)

- **User cancelled transaction** — 1,795 failures (15.79% of all failures)
- **Incorrect UPI PIN entered** — 1,697 failures (14.93%)
- These two user-side reasons together account for **30.72%** of all failures
- **Bank server timeout** — 839 failures (7.38%) — the largest single technical cause
- **App crash / session expired** — 818 failures (7.20%)

---

## Bank Processing Reliability (STG5)

![STG5 payer bank failure rate](figures/stg5-payer-bank-failure-rate.png)
![STG5 failure by bank tier](figures/stg5-failure-by-bank-tier.png)

| Bank Tier | Failed / Total | Failure Rate |
|---|---|---|
| Tier-1 | 1,250 / 125,877 | 0.99% |
| Tier-2 | 625 / 45,221 | 1.38% |
| Tier-3 | 598 / 19,148 | **3.12%** |

**Tier-3 banks fail at STG5 at roughly 3.15x the rate of Tier-1 banks.** The five worst-performing individual banks (Jammu & Kashmir Bank, DCB Bank, RBL Bank, Ujjivan Small Finance Bank, Bandhan Bank) all fall in the 3.2-3.6% failure range at this stage alone — a clear, prioritizable target list rather than a vague "improve reliability" recommendation.

---

## Device OS and Transaction Type

![Device OS failure rate](figures/device-os-failure-rate.png)
![Transaction type failure rate](figures/transaction-type-failure-rate.png)

| Segment | Failure Rate |
|---|---|
| Android | 5.76% |
| iOS | 5.74% |
| P2P | 5.81% |
| P2M | 5.71% |

Neither device OS (0.02 pp difference) nor transaction type (0.10 pp difference) materially affects failure rate — a useful negative result, ruling out two segments so investigation effort is correctly directed toward bank tier and funnel stage instead.

---

## Recommendations

1. **Fix STG3 (PIN authentication) first** — the single largest drop-off point. Focus: clearer PIN-retry UX and reduced accidental cancellations.
2. **Fix STG5 (bank processing) second**, with priority escalation specifically for Tier-3 banks, which fail at 3x the Tier-1 rate.
3. **Reduce Business Decline volume** (5,456 failures) through better balance/limit messaging before submission, not just after failure.
4. **Address Technical Decline** (4,173 failures) via timeout monitoring and faster bank-side escalation paths.
5. **Close the error-mapping gap** — 228 failures (2.01%) have no traceable cause; every failure should be attributable to a specific reason.
6. **Treat Deemed Approved as a distinct, high-priority track** separate from the volume-based ranking above, given its outsized impact on customer trust and support cost relative to its frequency.

---

## Conclusion

The funnel completes at a 94.25% rate overall, but that number masks a concentrated, addressable problem: **53% of all failures occur at just two of seven stages** (PIN authentication and bank processing), and **failure risk at the bank-processing stage is highly predictable by bank tier** (Tier-3 banks fail 3x more often than Tier-1). This reframes the fix from "improve UPI reliability" in the abstract to two specific, measurable interventions — authentication UX and Tier-3 bank escalation — that would address more than half the drop-off on their own.

---

## Project Structure

```
upi-payment-funnel-analytics/
├── data/                  # raw multi-table CSVs
├── sql/                   # schema creation, cleaning, and join scripts
├── notebooks/             # Python analysis notebooks
├── figures/               # exported charts referenced in this README
├── reports/               # PDF executive summary
└── README.md
```

## Data Sources

Bank-level failure-rate parameters were calibrated against NPCI's publicly published bank-wise Business Decline / Technical Decline statistics. Transaction-level data is synthetically generated, since transaction-level UPI data is never publicly available (commercially sensitive and regulated) — full methodology and calibration logic documented in `notebooks/01_data_exploration_and_cleaning.ipynb`.