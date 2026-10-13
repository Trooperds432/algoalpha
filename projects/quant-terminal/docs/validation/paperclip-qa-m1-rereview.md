# ALG-375 independent M1 re-review

Verdict: PASS ? the three ALG-374 blockers are closed for the source hashes below.
This is a bounded re-review, not an instruction to advance the milestone.

- Date: 2026-09-25 UTC.
- Issue: ALG-375 / 7dec6039-7f74-4c51-8259-14278bdf6d7f.
- Actual run: 015f92b0-3bf7-4981-bd54-f4147302b15a.
- Reviewer: role 27.4, agent 083f0ec4-4a80-4d95-b57e-7ed5825e3dd0.
- Harness checkout was already claimed. No second checkout or delegation.
- Workspace: C:/Users/Jonat/algoalpha/projects/quant-terminal.
- Files are untracked in the shared repository; hashes identify the reviewed source.

## Closure evidence

1. **Local automatic CI: PASS.** The foundation report records an actual watcher-triggered failed run followed by automatic success. Preserved checks-20260925T145246762097Z.json records Ruff I001 in test_core.py; checks-20260925T145256268946Z.json records all five stages passing and 28 tests. All 13 source/test hashes in the latter match this review's source before and after checks. Independent bounded probes of the actual watcher module, with subprocess and time mocked, verified initial dispatch, no repeat on unchanged fingerprints, dispatch after a change despite the previous failure, the exact check.py command and working directory, and --once propagation of both success and failure. Temporary scratch fixtures verified fingerprint changes for src Python, test Python, pyproject.toml and requirements-dev.lock. No continuous watcher was started by this reviewer. Historical automatic launch provenance is the coordinator's report; the JSON itself does not encode the triggering process. The consistent dated results, matching source and independently exercised watcher support closure under the roadmap's local-automation allowance. This is not hosted CI evidence. Scripts, docs and workflow edits themselves are outside the watcher's fingerprint surface.

2. **Event timing contract: PASS.** docs/domain_model.md now explicitly assigns observed_at to generic events and available_at to source market bars, with market observation no earlier than bar availability. Economic event_time remains separate. Event and strict serialization retain schema_version 1 and observed_at; there is no field rename or available_at alias. test_event_causal_roundtrip asserts observed_at is serialized, available_at is absent, canonical round trips work, and observation before economic time fails. Bar availability enforcement belongs to later implementation and is not claimed as an M1 capability.

3. **Clock restoration typed failure: PASS.** SimulationClock.restore rejects non-string snapshots with DomainError and normalizes ISO parse ValueError to DomainError; naive timestamps fail through utc's DomainError. The parameterized regression covers malformed text, naive text, None and integer 42. All four pass in the fresh suite and in independent stdin assertions requiring DomainError. Valid snapshot replay remains covered by existing tests.

## Fresh verification and retained limitations

- Ran `.venv/Scripts/python.exe scripts/check.py` exactly once on final source: exit 0. Ruff lint and formatting passed (13 files); mypy passed (10 source files); pytest reported 28 passed in 0.45 seconds; pip check reported no broken requirements. Generated evidence: docs/validation/checks-20260925T145416980724Z.json. Pytest reported one cache-write permission warning for .pytest_cache/v/cache/nodeids; retained unchanged.
- Ran `.venv/Scripts/python.exe coordination/check_m0.py`: exit 0; eight documents, seven diagrams and 370 unique assignment/issue/artifact mappings passed. The checker explicitly treats the pre-implementation constraint as historical after advancement. Assignment counts are not claims of actual agent participation.
- Package frontend attempt `python -m build --no-isolation --outdir <scratch>/dist <scratch>/package-source` failed: `No module named build.__main__; 'build' is a package and cannot be directly executed`. This failed attempt is preserved here.
- Fallback used the configured backend directly, with the same interpreter and `-B`, in a scratch copy of src and pyproject.toml: `from setuptools.build_meta import build_sdist, build_wheel; print(build_sdist('../dist')); print(build_wheel('../dist'))`. Exit 0; produced algoalpha_quant_terminal-0.0.1.tar.gz and algoalpha_quant_terminal-0.0.1-py3-none-any.whl. All ten wheel Python modules were byte-compared with final source and matched. This verifies backend packaging, not a fresh installation, isolated dependency resolution or build-from-sdist. Scratch omitted README, causing a standard README warning; -B caused byte-compilation warnings. No dependency was fetched. Scratch build artifacts are ephemeral; their hashes are recorded below.
- All reviewed source/test/script/config/document hashes in the initial snapshot remained equal after verification. This comparison does not exclude a transient concurrent edit between snapshots.

Only this report was authored in the project. The explicitly required check command generated its dated JSON and normal tool caches; no production edits, test edits, milestone edits or M2 implementation were made. Prior failed evidence remains intact. The coordinator owns integration and gate disposition. No trading, external services, market data or paid services were used. Data publication/sample dates, transaction costs, economic metrics and historical simulation coverage do not apply to these synthetic software checks.

## SHA-256 evidence

```text
.github\workflows\ci.yml 2b2282db8f10e213905cc70c56a1178601361a32b5d06fad974418820b54dadc
coordination\check_m0.py c7018bade36e6185ac47f55c03c97d1c4fe1e9e0e712f0d94a5d649bc91edb42
docs\domain_model.md fa36097dc8499618d8322c5a0be597363a286fb396b0e4fb37d5269c0f0034f4
docs\roadmap.md acea3eda9c950eabc5546dabf84aef3121041c615d9166b9b01503d7d49e840b
pyproject.toml df5797416864db5db1bd53d5ffb095afa99e293b7b6e0a998e3911286c44b5a0
requirements-dev.lock 90d4d729808385e342b80e0a18560ea07edf98b4b157fd861f52bc2093750157
scripts\check.py ccae6f08af1c90dba5835d801d3aea369f9dbb8a0b664e990cec4861295a5d44
scripts\watch_ci.py 2a644a327fddfcfc3c279942470c8be5113443c23ebc3a1fa8d7196f9ddf4913
src\quant_terminal\__init__.py fac90e3e4543031b65eaf1b3022920e7453b55549874d953504fca4017c0af0f
src\quant_terminal\config.py 70ac764303cc8643be25bafc60b652ea43beb4c4f4a466b021e5381372450ac8
src\quant_terminal\core\__init__.py 89d45cf461972de09c433a629a8c967b49afa141946c08faa541a1b4ddec23e6
src\quant_terminal\core\clock.py 002333d02c512d125875930c28444bab379593da57258f53333722105a3bd5b3
src\quant_terminal\core\enums.py ff555bf1355e38b0ab144a0d889757a76cc0cc9b431d64868b73761626be3a50
src\quant_terminal\core\events.py a41fa16b325325b1058648a650d73d332667eda4ac5a9f332bbd88615babc84b
src\quant_terminal\core\exceptions.py 879dcbacef34305cc520d42986f8fa11c89e8f885043d2845a783d7943dfd711
src\quant_terminal\core\identifiers.py 443c98476bdb0ff8b5f2b38179abbd6056c37c317903f9669a1f8129cca60d84
src\quant_terminal\core\serialization.py 3e462da3f422033f0620d8c9cafa0c401790e14ed17eaebb8be3a0d7dc3e469a
src\quant_terminal\logging.py 18d1199874e705790971ef0d3bf61f4413c4bd4ee0fbf40ec4d9be6ea461fd4e
tests\integration\test_foundation.py 45ce6ccc1031bffc99816c06ea161b3747a1260e689b5d1ab617e43124e67163
tests\unit\test_config_logging.py a6cddd6d62de8bad7ec5946fa347b3578a2519d67f06bcebc2d81a3be55e0df3
tests\unit\test_core.py 5c67d333b573b55e0b89d22bf7186b3fd2d816e779cd1e65f113f13601f91ba3
checks-20260925T145256268946Z.json 9ff23e97303c33c0106656ac4c929151c2c596437e5285f9995883ee07105c0d
checks-20260925T145416980724Z.json f2f9515fa74da08b1660000b1d59491c7c8c366dabe5f033b315e3131cc1affb
algoalpha_quant_terminal-0.0.1-py3-none-any.whl 2a4640fd70b11b97e53aaa0b263f43ebc0a4c3d7ea6bf888fa3a1deb9f935b66
algoalpha_quant_terminal-0.0.1.tar.gz 168e86d1d6ec4e729509800c17a985367ddad11d8e6ccfeaa85d81b9ec7d52f1
```
