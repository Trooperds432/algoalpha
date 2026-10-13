# Security and authority model

Local single-owner research is the initial deployment. API binds loopback when built.
No live execution is enabled by installing the terminal. Trading 212 demo setup is
separate from forward testing; no test orders or credential ingestion in M0.

Trust boundaries: imported datasets and documents; Python strategy code; API callers;
job workers; artifact store; Paperclip agents; any later broker gateway. Validate
schemas/sizes/paths, escape HTML output, prohibit traversal, and use opaque registered
strategy identifiers rather than arbitrary paths from HTTP input.

Before M16, only owner-reviewed trusted local Python strategies are supported. A frozen
object, restricted import hook, subprocess or timeout is not a sandbox against hostile
code. Design data capability filtering now; verify denied future requests. Before
arbitrary Python support, enforce filesystem, network, process, memory and CPU controls
in a tested isolation environment. Workers must not inherit secrets or the full dataset.

Agent IDs in requests are attribution, not authentication. M13 introduces per-agent
credentials stored outside project files, scoped service operations and audit trails.
Research job submission cannot alter risk limits, broker configuration or other agents'
permissions. Risk/validation actions remain independent. The project root's shared
Windows account is not a security boundary between 370 agents; disclose this limitation.

Use OS-protected credentials for a future gateway only; never pass them to strategies,
LLM prompts, issues, fixtures or reports. Paper/live credentials and endpoints separate.
No live host fallback on demo authentication failure. Reject redirects carrying auth.
Unknown submission outcomes require reconciliation; no automatic order retry.

Resource limits: bounded worker count, per-job timeout and disk/artifact limits; request
validation; job cancellation; no infinite agent polling. Idempotency and single ledger
writer prevent duplicate fills. Unknown symbols, stale data, invalid capital and missing
risk config fail closed. Logs redact secrets and exclude authentication headers.
Dependency provenance, pinned versions and license review are required before reuse.
Do not copy reference-engine code without evaluating its licence. Legal roles produce
internal research, not legal sign-off or regulatory filings.

Threat tests by milestone: M1 invalid config/log redaction; M2 future data/path traversal;
M4 duplicate fills/risk reservations; M8 overwrite/crash recovery; M13 auth/idempotency;
M16 hostile strategy escape/resource exhaustion; M17 restart/reconnect reconciliation;
M18 endpoint isolation/kill switch; M21 dependency audit, restore and load tests.
