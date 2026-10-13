# Isolated publication qualification, 2026-10-08

ALG-472 (ca01f8f7-3e2d-4015-b194-53dade928abb), coordinator-owned
`application/service.py` and `test_isolated_qualification.py`.

## Problem and behavior

Experiment registration and the isolated job decision use separate databases.
A process exit after immutable registration but before committing the successful
job decision can leave an artifact beside a running/interrupted job. Public
research must not treat that registration as confirmed completion.

The application service now qualifies an isolated manifest against a read-only
completion query. Either isolated strategy identity marker invokes qualification;
the run must have its generated run-32hex identity, a supported job schema (0/1),
an isolated-backtest job in succeeded state, and a result exactly equal to the
entire immutable manifest. Missing/corrupt/malformed metadata fails closed.
Connection closing is explicit; public reads do not initialize completion schemas.

Unconfirmed runs are omitted from the completed-run listing. Direct detail,
metric/report sections, artifacts, HTML and comparison reject with IntegrityError,
mapped to HTTP 409. Completed isolated runs remain accessible. Trusted built-in
behavior remains unchanged. Immutable files are preserved for operator recovery;
the interruption is not automatically re-executed or relabelled successful.

## Regression and review evidence

The first deterministic crash regression used the actual immutable registry and
JobStore transaction, raising a BaseException after publisher registration and
before job commit. Before the fix it exposed the run publicly. Mismatched success
metadata also failed the initial regression. One initial positive comparison test
incorrectly reused the same run ID; it was corrected to two distinct successful runs.

An independent reviewer reproduced an uncovered malformed-result JSONDecodeError
and identified read-side JobStore schema initialization. The guard was changed to
SQLite mode=ro with explicit closing and error handling. New malformed-result and
schemaless-database regressions passed. The reviewer independently passed 24 affected
tests, Ruff and mypy, and reproduced malformed JSON/NaN/non-object/blob/null rejection,
missing/corrupt database rejection, and byte-identical schemaless storage after reads.
Its first expanded harness left its own SQLite connection open during temporary
cleanup; explicit harness closing passed. This was a reviewer fixture failure.

A future-schema regression then demonstrated that user_version=2 still qualified.
The read-only guard now checks the same supported 0/1 versions as JobStore. Fresh
qualification/gateway tests passed 25 with one existing Starlette warning; focused
Ruff/format/mypy passed. That schema change receives fresh integrated verification.

`checks-20261008T185601169538Z.json` passed 817 tests plus lint/format/types/dependencies
before the final future-schema regression. Earlier six lifecycle reports and the
36c82d candidate archive predate qualification; their own evidence remains preserved,
but they do not approve the changed full source.

## Actual runtime evidence

`m22-real-gateway-20261008T185934Z.json` passed compatible current-source HTTP/CLI
replay and observed running HTTP cancellation. Default admission remains disabled.

`m22-publication-crash-20261008T185646Z.json` passed an actual disposable API process
exit at code 75 immediately after registration. The real container runtime was
closed before publication. The registry retained its report while the job decision
remained running. A normal API restart recovered the job as interrupted, hid it from
completed runs, rejected public detail/metrics/report/comparison, and retained the
exact report bytes. Container inventory was empty. This report predates the final
schema-version addition; a compatible final-source rerun also adds HTML and direct
artifact endpoint coverage.

## Remaining acceptance

Fresh final-source process-crash evidence, broad checks and independent review are
being completed. The qualified candidate must be rebuilt and its installation replay
repeated before final package acceptance. Final security/release review and the
remaining 318 employee findings still prevent final M16/M21/M22 release approval.
No broker connection, order, strategy forward test or final gate is authorized by
these software fixtures.

## Final-source acceptance

`checks-20261008T190315115004Z.json` passed 818 tests with 35 documented skips and
one existing Starlette warning. All lint/format/types/dependency checks passed.
`m22-publication-crash-20261008T190205Z.json` passed the compatible final-source
actual process-exit case, including six HTTP 409 rejections: detail, metrics, report,
HTML, direct report artifact and comparison. Exact immutable bytes remained intact,
and final container inventory was empty.

Independent reviewer `/root/m22_gateway_review` accepted ALG-472's bounded fix,
freshly passed 25 affected tests, matched all 103 source hashes in both actual
runtime reports and the 162-file broad-check inventory, and corroborated interrupted
job/no result, immutable registration, successful-manifest access and six rejection
responses against durable state and API logs. An initial reviewer lookup assumed
an unhashed artifact folder; the correct hashed directory convention verified bytes.

Rebuilt explicitly nonfinal candidate:
`C:/Users/Jonat/algoalpha/runtime/quant-terminal-m22-20261008-qualified.zip`, SHA256
261de2a82067a11093231e6f54d1463a98bf040dad2dd1ef453d705a21f18709.
Manifest/checksum verification passed, and it was extracted into a separate new installation,
which installed offline from its frozen lock. `m22-clean-gateway-20261008T190746Z.json`
preserves an initial smoke failure caused by requesting HTTP before the cold server
finished startup. The harness now waits for readiness; the server subsequently started.

`m22-clean-gateway-20261008T190828Z.json` passed installed health/readiness/capability/UI,
all installed source hashes, and an actual clean-environment isolated CLI replay with
the development fingerprint. Default isolated submission remained disabled. The
temporary smoke server was stopped; normal terminal and Paperclip remain separate.
Final security/release review and all-role participation are still required. Existing
reports, old candidates and failed receipts remain preserved; no final release passed.

After the narrow coordinator report-read validation change, the fresh integrated
`checks-20261008T191908608967Z.json` passed 824 tests, 35 documented skips and the
same existing warning, plus Ruff/format/mypy/dependency checks. Runtime/package
source bytes did not change. Independent role participation is now 53/370.
