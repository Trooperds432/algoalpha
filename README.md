# AlgoAlpha

Paper-only quantitative research firm. Installed Paperclip 2026.916.1 with 45 custom agents using codex_local, engine auto (ACP), existing Codex ChatGPT authentication and no model override. Dangerous sandbox/approval bypass is disabled.

Dashboard: http://127.0.0.1:3100
Company ID: 3b1d401d-7010-4eed-acd1-be7d52372c07
Verification issue: ALG-1 (completed successfully)

## Start and use

The local server is running. After a restart, double-click Start-AlgoAlpha.cmd and leave its terminal running. Windows background-service management is not supported by this Paperclip version. The data directory is .paperclip inside this workspace.

Open the dashboard, select AlgoAlpha, and assign FIRST-CEO-TASK.md to the CEO to begin research. No research cycle has been started by the installer. Agents are task-driven; scheduled heartbeats are disabled by the installed defaults.

## Owner-controlled thresholds

| Phase | Minimum Sharpe | Maximum drawdown |
| --- | ---: | ---: |
| Discovery | 1.0 | 25% |
| Promotion | 1.5 | 15% |
| Production (paper only) | 2.0 | 10% |

The 10% production ceiling is the stricter end of the owner's 10-12% range. No capital deployment is configured. The benchmark is intentionally unresolved and must be proposed before comparative evaluation.

config/risk-thresholds.json and config/firm-context.json are read-only operational guardrails, not security boundaries. Agents must never edit them.

Risk and Compliance report directly to the Board/owner. Independent backtesting and risk review precede controlled paper simulation. At least six months historical testing and documented out-of-sample costs are required. Thirty actual days of paper simulation are needed before further paper eligibility; installation does not satisfy this requirement.

TradingView MCP: existing installation reviewed and dependencies verified. Codex global registration exists; project configuration enables chart-reading tools. Health check passed with CDP on 127.0.0.1:9222 and chart API available. Existing rules.json was preserved. Subscription/data entitlements are not verified. Use only permitted data for research.

All broker, exchange, wallet and live-account access is prohibited. Corporate and physical-workplace roles prepare internal plans only. Failed strategy results are preserved, never hidden or silently retuned.

## Verification

logs/agent-activity/installation-verification.json records the fresh checks of company, 45 unique agents, adapter/cwd, reporting lines, instructions, CEO IDs, disabled bypass, staged thresholds, and completed agent-authored smoke test with issue comment. To repeat the checks while the server is running: node config/verify-installation.mjs

The dashboard returned HTTP 200. Automatic browser opening was unavailable in the installer session; open the URL manually.


## ETF market-maker project
The seven-deliverable paper-only desk is in projects/etf-market-maker. See its README.md, WORKFLOW.md and Run-ETF-Desk.cmd. Three desk roles were added (Validation, Portfolio Manager and Reporting); the firm had 45 agents before the expanded V2 roster. Synthetic engineering demo is complete; strategy promotion is rejected pending real evidence.

## Expanded V2 roster

AlgoAlpha now has 370 agents mapped one-to-one to the owner-supplied V2 role list. The original 45 agent IDs and histories are preserved, and 325 agents were added. Full descriptions and reporting lines are in config/team-plan.json; the reassignment audit is in logs/agent-activity/roster-v2-summary.md. All 370 have the existing 22-skill configuration. This roster update does not start agent work or trading.
