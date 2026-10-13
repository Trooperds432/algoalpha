# TradingView MCP verification

- Existing C:/Users/Jonat/tradingview-mcp-jackson installation preserved (no .git directory).
- Reviewed README.md, SECURITY.md, package.json, launch scripts, server entry point, connection and health code.
- Lockfile contained no install hooks; all dependency artifact hosts were registry.npmjs.org.
- npm install --ignore-scripts --no-fund --no-audit completed successfully.
- Existing rules.json and global Codex MCP registration preserved.
- AlgoAlpha project configuration and current Paperclip company Codex home configured with 18 chart-reading/inspection tools. No generic UI evaluation, order tools, or broker panels included in that allowlist.
- tv_health_check passed: cdp_connected=true, api_available=true, BATS:AAPL, daily chart.
- Debug listener verified at 127.0.0.1:9222 only.
- Official installed Windows Store TradingView package launched successfully. The older unsigned local copy was blocked by Windows Application Control and was not used; no security policy was modified.
- Some optional tool implementations call TradingView-owned services; repository-wide claims of zero network calls are not accurate.
- Subscription entitlements and market data licensing were not verified.
- No orders, broker connections or changes to risk thresholds were made.
