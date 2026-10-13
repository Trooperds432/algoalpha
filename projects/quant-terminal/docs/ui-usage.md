# Local research UI

Build the frontend from `apps/web`:

```powershell
cd apps/web
npm ci
npm run build
cd ../..
```

Create a labeled synthetic dataset if the state directory is new, then start the
loopback research API from the repository root:

```powershell
$state = Join-Path $env:LOCALAPPDATA 'AlgoAlpha\research'
.\.venv\Scripts\python.exe examples/seed_research.py --state-dir $state
.\.venv\Scripts\python.exe -m quant_terminal --state-dir $state serve --port 8765
```

Open `http://127.0.0.1:8765/ui/`. The backend serves the built assets under
`/ui/`; a missing build returns a 503 message. The UI and CLI read the same state
directory, and all research operations use the M13 application service. Use one
scheduler for a state directory at a time.

For frontend development, run `npm run dev` in `apps/web` while the backend is
running. Vite serves `http://127.0.0.1:5173/ui/` and proxies API routes to the
loopback backend. The frontend build and tests run with `npm run build` and
`npm test`.

The example data is synthetic and only exercises the research workflow. A job's
completion is not validation, approval, or a capital allocation decision. This
version has no brokerage order route or Trading 212 forward-testing workflow.
