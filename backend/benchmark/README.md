# Golden Benchmark

`cases/` contains fixed, version-controlled preference inputs. They are inputs plus invariant labels, not hand-authored expected prose. Run from `backend`:

```powershell
$env:ENABLE_DEV_MODES="1"
.\.venv\Scripts\python.exe benchmark_runner.py --mode MOCK
.\.venv\Scripts\python.exe benchmark_runner.py --mode REAL_LLM
.\.venv\Scripts\python.exe benchmark_runner.py --mode REPLAY --source-map benchmark\replay-sources.json
```

MOCK currently completes the Hangzhou fixtures and intentionally pauses cities without a fixed fixture. REPLAY requires a map from case name to a stable source job. Reports are written to `benchmark_runs/`; commit selected baseline reports when comparing versions.
