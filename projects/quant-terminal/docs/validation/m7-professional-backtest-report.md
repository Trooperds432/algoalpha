# M7 professional backtest report gate

Status: **PASSED** on 2026-09-27.

## Delivered

A deterministic report snapshot and offline export bundle provide the eleven requested report sections, typed metrics and explicit unavailable states, fifteen static SVG charts, and normalized tables for NAV, session returns, chart data, metrics, risk, trading statistics, exposure metrics, drawdowns, execution costs, matched FIFO trades, positions, orders, fills, benchmark, and exposure. Bundles include self-contained HTML, complete JSON, CSV and Parquet per table, and a SHA-256/size/row-count manifest. Decimal values are kept exact as text in machine exports, timestamps are UTC, existing destinations are rejected, and construction uses a staging directory followed by atomic finalization.

The report consumes one immutable backtest result and M6 analytics conventions. Matched trades are FIFO slices with pro-rata fees; order executions are not mislabeled as round trips. The report identifies price-only benchmarks, unavailable comparative statistics, partial calendar periods, sampled rather than intrabar MAE/MFE, nonpositive-NAV log gaps, and other limitations. HTML is offline and escaped; sensitive metadata key variants are redacted. Spreadsheet-leading CSV strings receive an apostrophe prefix.

## Verification

The full repository gate in [`checks-20260927T123201044339Z.json`](checks-20260927T123201044339Z.json) passed: Ruff lint, Ruff formatting, strict mypy over 36 source files, all **139 tests**, and `pip check`. Focused reporting tests passed **5/5**. Independent M7 quantitative review passed report/chart semantics. An independent implementation review initially found metadata key normalization did not cover punctuation/spacing variants; the fix is in the current reviewed code. The re-review passed, including `api_key`, `api-key`, `api key`, `API KEY ID`, and `secret_key`, with tests asserting secret values are absent from HTML and JSON.

| Evidence | SHA-256 |
|---|---|
| `docs/m7-report-contract.md` | `118AEF92055CDF71BD0B161BA425514A5552C21B9952F5D83DCF97ACBC835509` |
| `src/quant_terminal/reporting/builder.py` | `2FAE3F4DF54824485C45CB70A30FF4EC36C73F5546B8E58C3ACAB3D0CF2F69AF` |
| `src/quant_terminal/reporting/export.py` | `C4C3260557731508AAF7777EBE93082882C9DE6F1E7E5BB3737846F3B25CFE4E` |
| `src/quant_terminal/reporting/html.py` | `96E9D3819861456952EE59B727D1723F40E8CF9C8FA9042C2977F25BBAC2DA9C` |
| `src/quant_terminal/reporting/models.py` | `D08D48AF7788305557FFDD7E974880A209801EE1FBB9275172A90D76EA12CED6` |
| `tests/unit/test_reporting.py` | `6BB9445B28C7A59F3397AF3D5F92B622D086144C72EB75BD72C9164DDC9A7531` |
| `docs/validation/checks-20260927T123201044339Z.json` | `272CB1B413A1BD89E154ED16969B16FE0D62A37F3835225D78CE9D674C76457E` |

PDF remains deferred by the owner brief. No broker integration, strategy evaluation, order submission, or deployment was part of this milestone.
