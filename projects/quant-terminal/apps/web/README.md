# AlgoAlpha research UI

From `apps/web`, install the locked dependencies with `npm ci`, run `npm run dev`,
and open `http://127.0.0.1:5173/ui/`. Start the local Python API on
`127.0.0.1:8765` first. Vite proxies the research API routes to that server.

`npm run build` creates `dist/index.html`, `dist/favicon.svg`, and `dist/assets/*`
for the Python server's `/ui/` route. `npm test` runs the request/response
boundary tests. Browser operations submit to the same M13 application service as
the CLI. The UI accepts only trusted built-in strategies and contains no broker
order control.

Charts convert exact response strings to JavaScript numbers for pixel positions.
Tables and metric tooltips retain the exact source strings; calculations and
ranking stay on the server. Sample data labeled synthetic is for research only.
