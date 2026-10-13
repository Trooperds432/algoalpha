# M3 portfolio and accounting gate

Status: **PASSED** on 2026-09-26.

The portfolio ledger applies immutable fills once, maintains signed FIFO lots,
allocates entry and exit fees to matched quantities, and produces cash, marked
NAV, realized and unrealized P&L, exposures, and leverage. Arithmetic is isolated
from caller Decimal settings at precision 34 with `ROUND_HALF_EVEN`. Fill and
mark operations roll back if a snapshot cannot be produced.

The hand-calculated suite covers long entries and partial exits, FIFO lot fee
allocation, short entry and cover with an explicit research-only assumption,
reversals, duplicate and invalid fills, generated fee-bearing long exits,
reconciliation, hostile caller Decimal context, and overflow mark rollback.
The owner-brief reversal fixture reconciles to cash 10,647.5, position -5,
realized P&L 98, unrealized P&L -0.5, and NAV 10,097.5.

The complete check is recorded in
[`checks-20260926T130321260119Z.json`](checks-20260926T130321260119Z.json):
76 tests passed, Ruff lint and formatting passed, strict mypy passed, and
`pip check` found no broken requirements. The independent read-only review
passed on the exact source hashes below and confirmed Decimal-context isolation,
fee allocation, and rollback behavior.

| File | SHA-256 |
|---|---|
| `src/quant_terminal/accounting/ledger.py` | `9B4B8D2FE5376752CBD96DBA0578F3BFD1DBE7E8CCBECF7C0C23A284A5646664` |
| `tests/unit/test_accounting_ledger.py` | `1F9F69BFD1E829546D8B1FD8A0BA8F2179669637D520A8BBB8069BA25744D97D` |

Limits: this is single-currency cash-equity/ETF accounting. Short permission is
only a research assumption; borrow, financing, margin, collateral, corporate
actions, and stale-mark policy are not implemented. No broker connection,
paper-trading order, or live trading is part of this milestone.
