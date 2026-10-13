# M10 walk-forward optimization gate

Status: **PASSED** on 2026-09-27.

## Delivered

Added immutable rolling and expanding fixed-duration UTC plans; IS-only optimizer capability; M9 campaign and M8 selection binding; OOS source/data/config validation; half-open timestamp validation; IS/OOS metric degradation; parameter stability; contiguous cancellation/resume prefix checks; and OOS-only NAV stitching. M10 compares the complete fixed M8 identity and pins all strategy/execution inputs across windows.

The OOS adapter follows the M0 reset convention: it must schedule liquidation against the final eligible OOS close with the configured execution and liquidity models. The core rejects residual positions or active orders, requires the first OOS capital to match the pinned experiment and every later/resumed OOS run to begin at the prior terminal equity, and stitches recorded NAV directly without rescaling. The caller-owned recorder retains the window prefix; storage policy remains adapter-owned. No broker/order submission is implemented.

The end-to-end integration instruments M2 query bounds, runs a real bounded M9 search per training window with append-only journals, registers M5/M6/M7 artifacts and M8 manifests, freezes selected parameters, and evaluates three later OOS windows including a clipped final window. It verifies funding continuity, raw NAV stitching, and rejection of a residual-position result. A second execution fixture holds a real position, liquidates on the final eligible bar, checks the close reference price, configured participation capacity and $2.00 commission total, and rejects zero-volume liquidation.

## Verification

The full repository gate in [`checks-20260927T154556032656Z.json`](checks-20260927T154556032656Z.json) passed: Ruff lint, Ruff format, strict mypy over 48 source files, **184 passed**, and `pip check`. Two symlink tests were skipped because Windows symlink creation is unavailable for this account. The independent final review passed the M0 liquidation/funding convention and independently ran the final-close/zero-capacity fixture.

## Evidence hashes

| Artifact | SHA-256 |
|---|---|
| `docs/m10-walk-forward-contract.md` | `10EF173C911B40C440714ED1C30B5188A80961467ED6F91288032CBF82C6485F` |
| `src/quant_terminal/walk_forward/engine.py` | `0BC007A6A442BAFA9053A226D45C933206CFC033CCB8AF40F6803C8AB71F8D06` |
| `src/quant_terminal/walk_forward/models.py` | `2A342AB41C8A1150535180349314F1EF754D79890AB736A39A31FB165F8E6FE4` |
| `tests/unit/test_walk_forward.py` | `8602CE06D42CD26A892EF334C62FBFC1A5C489CDD8B6B43DD137BC7C51541489` |
| `tests/integration/test_experiments.py` | `F12719A2CB2DDAC0F437E0FBE1C5825FEFFB5254B8952E173B4D419329C45967` |
| `docs/validation/checks-20260927T154556032656Z.json` | `92F463D56A4D49E047ADB6A3606AA4876BB8BDD9B03D2E60F347635F963F32DA` |
