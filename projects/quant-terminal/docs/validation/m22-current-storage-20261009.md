# Current installed candidate storage evidence — ALG485

Issue `b773c72d-09f4-401f-aa36-f425d14982cc`. Bounded offline evidence only; no milestone or final release approval.

The audited-fixed candidate SHA256 is `20bf8fe18ee998cd17576cffb09d2ad935bca6a4b49c32a0eb0340c5cfc926bd`. Independently checked all 127 installed manifest files against that archive: zero mismatches. All CLI operations used the candidate's own Python at `C:/Users/Jonat/algoalpha/runtime/m22-audited-fixed-clean-install-20261008/.venv/Scripts/python.exe`, from its immutable installation directory. Neither candidate nor production source changed.

Source fixture was the stopped synthetic gateway state identified by `m22-real-gateway-20261008T195348Z.json`. A new private runtime directory received a byte-identical copy of 33 files. No original file was replaced or deleted. No service, broker, Podman execution or strategy execution occurred.

Passing report: `m22-current-storage-20261009-20261009T122301Z.json`. The helper `coordination/m22_check_current_storage.py` SHA256 is `166329a83af50e9498b9f4cd18fdebe7761390a94def3fb56ea45d618751084c`.

- Installed CLI offline backup and absent-destination restore passed.
- Normal `jobs.sqlite`, `isolated-jobs/jobs.sqlite` and `runs/experiments.sqlite3` were inspected, set to legacy version 0 only on the restored private copies, upgraded transactionally to version 1, and inspected again. All logical rows remained equal.
- The restored registry, public run details, result fingerprints and report bytes equal the pre-backup copy. Three actual succeeded isolated runs remained qualified: `run-3cbc5cfd94ca4d569535501b74c2d099`, `run-e8f0f6a78e914d4f8f9c1e7efc76a744`, `run-f3d4c01ee8e34e93aa57e7f94c8ed4ce`.
- The retained publication-crash orphan `run-935eaac6eda343a7b0181cd6e6fbd586` remained unqualified. Backup/restore did not promote interrupted research into completed research.
- All 18 immutable dataset/run files preserved exact hashes. All 33 original fixture hashes remained unchanged.
- Future version 2 isolated completion database, on another private copy, rejected with DomainError/exit 1; the entire database hash remained unchanged.

The first report `m22-current-storage-20261009-20261009T122218Z.json` is preserved as failed: its harness expected future-schema rejection exit 2, whereas the installed CLI correctly returns exit 1. Backup, restore, migrations and report qualification had already passed. Only that harness expectation changed before the fresh run on a different owned copy; no product fix was needed.

This is stopped-writer recovery evidence, not a live consistent snapshot service or proof that backup fences every writer. All normal, isolated and paper writers must be stopped. The existing backup explicitly probes only the normal scheduler lease; the isolated lock is retained as a harmless unlocked file on restored state. This check did not exercise paper or risk databases because neither existed in this fixture. Their existing tests and earlier candidate evidence remain separate.

All scoped helper/report files are frozen after this record. Remaining employee interaction and consolidated security/final release requirements are unchanged.
