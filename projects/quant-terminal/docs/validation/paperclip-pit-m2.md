# M2 independent point-in-time review

Verdict: **PASS for the bounded M2 point-in-time review after correction.** PIT-1 was reproduced, then resolved by a concurrent implementation update and independently rechecked in this run. This is not an overall milestone gate decision. The review assignment is complete.

Reviewer: role 22.2; agent fb8f832d-b1a0-407d-ade1-a695c79d4482.
Issue: ALG-377 / c37f302e-7971-4651-9628-a5b7fdc6c6d2.
Run: 121ce2b9-919b-4e98-adf7-d0b718afb38c. Review date: 2026-09-25.
Checkout was supplied by the harness. Only this repository report was written by
this reviewer; synthetic probe files were confined to run-owned scratch.

Read: AGENTS.md, docs/roadmap.md, coordination/milestone-state.json,
docs/sources/owner-brief.txt M2 lines 660–706, docs/m2-data-contract.md,
docs/validation/m2-historical-data.md, data model/calendar/storage/feed source,
core clock, and data integration tests. M2 remains active.

## Preserved initial finding PIT-1 (resolved during this run)

The initially reviewed DatasetStore.load_snapshot excludes parquet_sha256 from the snapshot-ID payload,
then trusts that mutable manifest field to verify the file. It never recomputes
canonical content_hash from stored rows. query_bars rechecks the manifest and
individual bar validity but does not close this gap.

Scratch-only reproduction: change the first close from 100.25 to 100.50 (valid
OHLC), rewrite Parquet and update only manifest.parquet_sha256. Leave snapshot_id,
content_hash, input_hash and all other manifest fields unchanged. The existing
DataFeed returns 100.500000000000. load_snapshot succeeds with the old snapshot ID
and content hash, despite the caller retaining the original frozen snapshot object.

Required owner/action: the M2 implementation owner/coordinator must bind the file
digest into snapshot identity, or recompute and compare canonical content and
manifest summaries before accepting a snapshot. Add a regression for paired
Parquet/checksum mutation, then obtain independent review on fresh source hashes.
A changed row must reject under the old identity; legitimate corrections need
distinct identities. This probe requires write access to both scratch files;
it does not demonstrate a bypass through public DataFeed methods alone.
The finding concerns data integrity, not adversarial Python isolation.

## Initial checks and results

- Targeted suite: 37 passed in 0.72 seconds. Command: project venv Python,
  `-m pytest -q -p no:cacheprovider tests/integration/test_data_feed.py tests/integration/test_data_storage.py tests/unit/test_data_model_calendar.py --basetemp <run-scratch>/pytest-pit`.
- Independent delayed-publication probe: first bar delayed to November 27 18:02
  UTC, second published at 18:01. Neither appears at 18:00; only the second appears
  at 18:01; both appear at 18:02. between agrees with history. A historical 18:00
  query stays empty after clock advancement.
- Both methods reject an end one microsecond beyond clock.now with FutureDataError.
  Naive timestamps reject. Bar end alone does not grant visibility.
- Unrestricted DatasetStore returns both rows before publication. This research
  capability must not be supplied to strategies. DataFeed private attributes and
  the mutable simulation clock are not an adversarial sandbox.
- Forged snapshot row_count and duplicate import reject. A changed close with a
  later available_at produces a distinct snapshot/content hash, preserving the
  original close. Snapshot selection remains a caller responsibility.
- Appending bytes to scratch Parquet rejects on an existing feed's next read.
  Updating a valid Parquet file and its checksum reproduces PIT-1 instead.
- Input hash matches CSV bytes. Manifest records source, price mode, publication
  delay, schema, calendar/holidays/tzdata and Arrow/DuckDB versions, count and
  interval. These are self-reported provenance, not vendor attestation.
- Source enforces available_at >= bar.end and the declared publication delay.
  Feed enforces bar.end/available_at <= request.end <= clock.now, including
  historical as-of queries after clock advancement.

## Limits and provenance

No ingestion timestamp, revision lineage, vintage fundamentals or index membership
engine is implemented here. Corrections require truthful available_at values;
a new snapshot hash cannot make backdated corrected prices point-in-time safe.
CorporateAction has known_at/effective_at; an action as-of feed/join is not delivered.

All data is repository-authored synthetic XNYS data dated 2026-11-25/27, with
deliberately delayed and revised publication times. These future fixture dates
are not observed market samples. No vendor, chart, broker or real-market data
was used. Costs, benchmark, Sharpe and drawdown are not applicable: this is data
infrastructure verification, with no strategy, returns evaluation or simulation.

No HEAD commit exists; git rev-parse HEAD failed and repository files are untracked.
No commit provenance is claimed. The originally cited automated evidence
checks-20260925T150521973250Z.json differs from reviewed feed.py and test_data_feed.py;
its 65-test result cannot attest this final source. Other 19 source/test hashes
matched that report. Subsequent changes require renewed validation.

## Final source binding

Verified at 2026-09-25T15:10:04.515568+00:00; all 21 source/test hashes stable before/after a repeated targeted run (same command, pytest-final scratch directory).

```text
......................................                                   [100%]
38 passed in 0.75s
```

Latest inspected automated report: docs\validation\checks-20260925T150948701010Z.json; hash mismatches: [].

| File | SHA-256 |
|---|---|
| src\quant_terminal\__init__.py | fac90e3e4543031b65eaf1b3022920e7453b55549874d953504fca4017c0af0f |
| src\quant_terminal\config.py | 70ac764303cc8643be25bafc60b652ea43beb4c4f4a466b021e5381372450ac8 |
| src\quant_terminal\core\__init__.py | 89d45cf461972de09c433a629a8c967b49afa141946c08faa541a1b4ddec23e6 |
| src\quant_terminal\core\clock.py | 002333d02c512d125875930c28444bab379593da57258f53333722105a3bd5b3 |
| src\quant_terminal\core\enums.py | ff555bf1355e38b0ab144a0d889757a76cc0cc9b431d64868b73761626be3a50 |
| src\quant_terminal\core\events.py | a41fa16b325325b1058648a650d73d332667eda4ac5a9f332bbd88615babc84b |
| src\quant_terminal\core\exceptions.py | 879dcbacef34305cc520d42986f8fa11c89e8f885043d2845a783d7943dfd711 |
| src\quant_terminal\core\identifiers.py | 443c98476bdb0ff8b5f2b38179abbd6056c37c317903f9669a1f8129cca60d84 |
| src\quant_terminal\core\serialization.py | 3e462da3f422033f0620d8c9cafa0c401790e14ed17eaebb8be3a0d7dc3e469a |
| src\quant_terminal\data\__init__.py | f37ecf92786ce13fe600b21d210ae9314f4485af24d2902f7408cbcec6956ce6 |
| src\quant_terminal\data\calendar.py | 6400d1922270962e0877ad5454c0e22ce45035dd6684525eaf9d360f02bdf4d3 |
| src\quant_terminal\data\feed.py | 0b1f838db447b27759bc3f388c1879b746b4aa6c8b3a493154db9512e2a9979b |
| src\quant_terminal\data\model.py | 48e4e93874db8729b1b1366b11f85f42821eae7fcca0840a0e71adc585a207fc |
| src\quant_terminal\data\storage.py | e58e1a0473e26547545039eb3511602ba9d732cf4feb8f0182e95ebd9d9d10c3 |
| src\quant_terminal\logging.py | 18d1199874e705790971ef0d3bf61f4413c4bd4ee0fbf40ec4d9be6ea461fd4e |
| tests\integration\test_data_feed.py | 8f8b2c089b174169cc3995499d9adf9fcf2b1cc6b063474a917fdf11f4a45257 |
| tests\integration\test_data_storage.py | bd037133a67b6a8623aab6d3ce7685486ee2d5c852bd41924f8d23651360fe9f |
| tests\integration\test_foundation.py | 45ce6ccc1031bffc99816c06ea161b3747a1260e689b5d1ab617e43124e67163 |
| tests\unit\test_config_logging.py | a6cddd6d62de8bad7ec5946fa347b3578a2519d67f06bcebc2d81a3be55e0df3 |
| tests\unit\test_core.py | 5c67d333b573b55e0b89d22bf7186b3fd2d816e779cd1e65f113f13601f91ba3 |
| tests\unit\test_data_model_calendar.py | 1228db5537ffbead297d0b2f4f2b26fb15147a643cbb328b2839d60445d7e2f2 |

## Reproducible independent probe

Run from repository root using the project venv, PYTHONPATH=src and PYTHONDONTWRITEBYTECODE=1. Set PAPERCLIP_RUN_SCRATCH_DIR to a fresh run-owned scratch directory. The script mutates only its generated snapshots. Its final assertion confirms the defect, not product correctness. All 16 printed checks passed, including defect reproduction.

```python
import csv, hashlib, json, os
from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import replace, FrozenInstanceError
from decimal import Decimal
import pyarrow as pa
import pyarrow.parquet as pq
from quant_terminal.data.storage import DatasetStore, CSV_COLUMNS, BAR_SCHEMA
from quant_terminal.data.model import InstrumentMeta
from quant_terminal.core.identifiers import InstrumentId
from quant_terminal.core.clock import SimulationClock
from quant_terminal.core.exceptions import DomainError, FutureDataError
from quant_terminal.data.feed import DataFeed
root=Path(os.environ["PAPERCLIP_RUN_SCRATCH_DIR"])/"pit-probes"
root.mkdir(exist_ok=True)
def dt(s): return datetime.fromisoformat(s)
inst=InstrumentMeta(InstrumentId("ETF-1"),"TEST","USD","etf","XNYS","America/New_York",Decimal(".01"),Decimal("1"))
rows=list(csv.DictReader(Path("examples/data/synthetic_xnys_2026.csv").open()))
def write(name, data):
 p=root/name
 with p.open("w",newline="") as f:
  w=csv.DictWriter(f,fieldnames=CSV_COLUMNS); w.writeheader(); w.writerows(data)
 return p
rows[0]["available_at"]="2026-11-27T18:02:00+00:00"
store=DatasetStore(root/"store")
snap=store.import_csv(write("delayed.csv",rows),instrument=inst,source="synthetic-fixture",publication_delay=timedelta(minutes=1))
clock=SimulationClock(dt("2026-11-27T18:00:00+00:00")); feed=DataFeed(store,snap,clock)
def check(name, cond):
 assert cond,name
 print("PASS",name)
def rejected(name, fn, kind=DomainError):
 try: fn()
 except kind: print("PASS",name)
 else: raise AssertionError(name)
check("bar ends do not imply publication",feed.history(end=clock.now,count=9)==())
check("store capability deliberately unrestricted",len(store.query_bars(snap,end=clock.now))==2)
clock.advance(dt("2026-11-27T18:01:00+00:00"))
check("later bar can publish before earlier bar",len(feed.history(end=clock.now,count=9))==1)
check("between same visibility",len(feed.between(start=dt("2026-11-25T00:00:00+00:00"),end=clock.now))==1)
for method in [lambda:feed.history(end=clock.now+timedelta(microseconds=1),count=1),lambda:feed.between(start=dt("2026-11-25T00:00:00+00:00"),end=clock.now+timedelta(microseconds=1))]:
 rejected("future request +1us",method,FutureDataError)
clock.advance(dt("2026-11-27T18:02:00+00:00"))
check("exact delayed publication visible",len(feed.history(end=clock.now,count=9))==2)
check("historical as-of stays hidden after clock advance",feed.history(end=dt("2026-11-27T18:00:00+00:00"),count=9)==())
rejected("naive request",lambda:feed.history(end=datetime(2026,11,28),count=1))
rejected("forged snapshot object",lambda:store.query_bars(replace(snap,row_count=99),end=clock.now))
rejected("identical import cannot overwrite",lambda:store.import_csv(root/"delayed.csv",instrument=inst,source="synthetic-fixture",publication_delay=timedelta(minutes=1)))
rev=[dict(r) for r in rows]; rev[0]["close"]="100.50"; rev[0]["available_at"]="2026-11-27T18:03:00+00:00"
new=store.import_csv(write("revision.csv",rev),instrument=inst,source="synthetic-fixture",publication_delay=timedelta(minutes=1))
check("revision distinct ID and canonical hash",new.snapshot_id!=snap.snapshot_id and new.content_hash!=snap.content_hash)
check("original revision unchanged",store.query_bars(snap,end=clock.now)[0].close==Decimal(rows[0]["close"]))
original=snap.parquet_path.read_bytes()
snap.parquet_path.write_bytes(original+b"tamper")
rejected("mutated file detected on existing feed",lambda:feed.history(end=clock.now,count=9))
snap.parquet_path.write_bytes(original)
manifest=json.loads(snap.manifest_path.read_text())
check("input checksum provenance",manifest["input_hash"]==hashlib.sha256((root/"delayed.csv").read_bytes()).hexdigest())
table=pq.read_table(snap.parquet_path)
values=table.to_pylist(); values[0]["close"]=Decimal("100.50")
pq.write_table(pa.Table.from_pylist(values,schema=BAR_SCHEMA),snap.parquet_path)
manifest["parquet_sha256"]=hashlib.sha256(snap.parquet_path.read_bytes()).hexdigest()
snap.manifest_path.write_text(json.dumps(manifest))
observed=feed.history(end=clock.now,count=9)[0].close
print("PAIRED_MUTATION",json.dumps({"same_snapshot_id":store.load_snapshot(snap.snapshot_id).snapshot_id==snap.snapshot_id,"old_close":rows[0]["close"],"observed_close":str(observed),"unchanged_content_hash":store.load_snapshot(snap.snapshot_id).content_hash==snap.content_hash}))
check("paired mutation reproduced",observed==Decimal("100.50"))
```

## Final re-review disposition

The concurrent implementation update adds full Parquet schema, row-count, metadata, publication-delay, ordering and canonical-content checks in load_snapshot. The stored canonical content hash is already bound into snapshot identity. I inspected the new code and reran the independent probe in a fresh scratch directory. All 17 checks pass; the paired mutation now raises DomainError on both the existing feed and load_snapshot. The final targeted suite above contains 38 passing tests, including a new paired-mutation regression. PIT-1 is closed for these final hashes; no remaining blocker was found within the assigned scope. The initial failure and reproduction remain above as experiment history. No other agent issue/run identity is inferred from the concurrent filesystem changes.

Final successful probe (same setup as original; fresh scratch root):

```python
import csv, hashlib, json, os
from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import replace, FrozenInstanceError
from decimal import Decimal
import pyarrow as pa
import pyarrow.parquet as pq
from quant_terminal.data.storage import DatasetStore, CSV_COLUMNS, BAR_SCHEMA
from quant_terminal.data.model import InstrumentMeta
from quant_terminal.core.identifiers import InstrumentId
from quant_terminal.core.clock import SimulationClock
from quant_terminal.core.exceptions import DomainError, FutureDataError
from quant_terminal.data.feed import DataFeed
root=Path(os.environ["PAPERCLIP_RUN_SCRATCH_DIR"])/"pit-probes-fixed"
root.mkdir(exist_ok=True)
def dt(s): return datetime.fromisoformat(s)
inst=InstrumentMeta(InstrumentId("ETF-1"),"TEST","USD","etf","XNYS","America/New_York",Decimal(".01"),Decimal("1"))
rows=list(csv.DictReader(Path("examples/data/synthetic_xnys_2026.csv").open()))
def write(name, data):
 p=root/name
 with p.open("w",newline="") as f:
  w=csv.DictWriter(f,fieldnames=CSV_COLUMNS); w.writeheader(); w.writerows(data)
 return p
rows[0]["available_at"]="2026-11-27T18:02:00+00:00"
store=DatasetStore(root/"store")
snap=store.import_csv(write("delayed.csv",rows),instrument=inst,source="synthetic-fixture",publication_delay=timedelta(minutes=1))
clock=SimulationClock(dt("2026-11-27T18:00:00+00:00")); feed=DataFeed(store,snap,clock)
def check(name, cond):
 assert cond,name
 print("PASS",name)
def rejected(name, fn, kind=DomainError):
 try: fn()
 except kind: print("PASS",name)
 else: raise AssertionError(name)
check("bar ends do not imply publication",feed.history(end=clock.now,count=9)==())
check("store capability deliberately unrestricted",len(store.query_bars(snap,end=clock.now))==2)
clock.advance(dt("2026-11-27T18:01:00+00:00"))
check("later bar can publish before earlier bar",len(feed.history(end=clock.now,count=9))==1)
check("between same visibility",len(feed.between(start=dt("2026-11-25T00:00:00+00:00"),end=clock.now))==1)
for method in [lambda:feed.history(end=clock.now+timedelta(microseconds=1),count=1),lambda:feed.between(start=dt("2026-11-25T00:00:00+00:00"),end=clock.now+timedelta(microseconds=1))]:
 rejected("future request +1us",method,FutureDataError)
clock.advance(dt("2026-11-27T18:02:00+00:00"))
check("exact delayed publication visible",len(feed.history(end=clock.now,count=9))==2)
check("historical as-of stays hidden after clock advance",feed.history(end=dt("2026-11-27T18:00:00+00:00"),count=9)==())
rejected("naive request",lambda:feed.history(end=datetime(2026,11,28),count=1))
rejected("forged snapshot object",lambda:store.query_bars(replace(snap,row_count=99),end=clock.now))
rejected("identical import cannot overwrite",lambda:store.import_csv(root/"delayed.csv",instrument=inst,source="synthetic-fixture",publication_delay=timedelta(minutes=1)))
rev=[dict(r) for r in rows]; rev[0]["close"]="100.50"; rev[0]["available_at"]="2026-11-27T18:03:00+00:00"
new=store.import_csv(write("revision.csv",rev),instrument=inst,source="synthetic-fixture",publication_delay=timedelta(minutes=1))
check("revision distinct ID and canonical hash",new.snapshot_id!=snap.snapshot_id and new.content_hash!=snap.content_hash)
check("original revision unchanged",store.query_bars(snap,end=clock.now)[0].close==Decimal(rows[0]["close"]))
original=snap.parquet_path.read_bytes()
snap.parquet_path.write_bytes(original+b"tamper")
rejected("mutated file detected on existing feed",lambda:feed.history(end=clock.now,count=9))
snap.parquet_path.write_bytes(original)
manifest=json.loads(snap.manifest_path.read_text())
check("input checksum provenance",manifest["input_hash"]==hashlib.sha256((root/"delayed.csv").read_bytes()).hexdigest())
table=pq.read_table(snap.parquet_path)
values=table.to_pylist(); values[0]["close"]=Decimal("100.50")
pq.write_table(pa.Table.from_pylist(values,schema=BAR_SCHEMA),snap.parquet_path)
manifest["parquet_sha256"]=hashlib.sha256(snap.parquet_path.read_bytes()).hexdigest()
snap.manifest_path.write_text(json.dumps(manifest))
rejected("paired mutation rejected on existing feed",lambda:feed.history(end=clock.now,count=9))
rejected("paired mutation rejected on reload",lambda:store.load_snapshot(snap.snapshot_id))
```


## Recovery verification ? 2026-10-03T11:58:00.337602+00:00

Issue ALG-377 (`c37f302e-7971-4651-9628-a5b7fdc6c6d2`); run `5bce882f-815f-4316-baf8-9a953b25ee22`; reviewer agent `fb8f832d-b1a0-407d-ade1-a695c79d4482`. Harness checkout reused.

PASS for the bounded point-in-time checks on the hashes below. This does not declare the final M2 gate passed. Earlier findings, experiments and run attribution above are retained as historical evidence. The earlier statement that M2 remains active describes the September review; current coordination state records active M22 and an existing M2 pass, neither modified by this review.

Fresh execution of the preserved final probe above passed all 17 assertions, including delayed and reordered publication, historical as-of visibility, future requests, revision identity, and paired Parquet/checksum mutation rejection on both reload and an existing feed. The focused pytest command recorded above was repeated with --basetemp set to this run scratch/pytest-recovery: **38 passed in 1.25s**. Source/test SHA-256 values were identical before and after execution. No production, test, or milestone-state files were edited.

The requested checks-20260925T150654724035Z.json is retained 65-test evidence from before the integrity correction: its storage and storage-test hashes differ from this review. It must not be presented as verification of the corrected storage. Current storage and tests match the repaired hashes already recorded above.

Timestamp policy: start/end describe the event interval; available_at supplies publication eligibility; both bar.end and available_at must be <= the requested as-of end, which itself must be <= the simulation clock. The manifest records source, input/content/file hashes, publication delay, schema and library/calendar versions. It does not prove vendor publication truth or retain a separate ingestion timestamp. This bounded bar engine is not a vintage fundamentals/index-membership dataset or general as-of join service. Corrections require new snapshots and truthful availability supplied by the importer; revision completeness across an external vendor archive is unmeasured. DatasetStore remains a trusted unrestricted research capability; Python object privacy is not adversarial isolation.

Data is the repository synthetic fixture for November 25 and 27, 2026, with probe publication times through November 27 18:03 UTC. These are constructed future-dated samples, not observed market coverage. No external data was acquired. Costs, benchmark, Sharpe, drawdown and strategy performance are not applicable to these data-integrity probes; no backtest or simulation eligibility is claimed. No remaining blocker was found within this bounded review.

| Reviewed file | SHA-256 |
|---|---|
| src\quant_terminal\data\calendar.py | 6400d1922270962e0877ad5454c0e22ce45035dd6684525eaf9d360f02bdf4d3 |
| src\quant_terminal\data\feed.py | 0b1f838db447b27759bc3f388c1879b746b4aa6c8b3a493154db9512e2a9979b |
| src\quant_terminal\data\model.py | 48e4e93874db8729b1b1366b11f85f42821eae7fcca0840a0e71adc585a207fc |
| src\quant_terminal\data\storage.py | e58e1a0473e26547545039eb3511602ba9d732cf4feb8f0182e95ebd9d9d10c3 |
| src\quant_terminal\data\__init__.py | f37ecf92786ce13fe600b21d210ae9314f4485af24d2902f7408cbcec6956ce6 |
| src\quant_terminal\core\clock.py | 002333d02c512d125875930c28444bab379593da57258f53333722105a3bd5b3 |
| tests\integration\test_data_feed.py | 8f8b2c089b174169cc3995499d9adf9fcf2b1cc6b063474a917fdf11f4a45257 |
| tests\integration\test_data_storage.py | bd037133a67b6a8623aab6d3ce7685486ee2d5c852bd41924f8d23651360fe9f |
| tests\unit\test_data_model_calendar.py | 1228db5537ffbead297d0b2f4f2b26fb15147a643cbb328b2839d60445d7e2f2 |
