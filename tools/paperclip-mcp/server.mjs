#!/usr/bin/env node
// Minimal stdio MCP server exposing the local Paperclip API (read-only).
// No dependencies: speaks newline-delimited JSON-RPC 2.0 per the MCP stdio transport.
//
// Guardrails: GET requests only, loopback base URL only, and paths that could
// return credentials (secrets, keys, tokens, logins, auth) are refused.

import { createInterface } from "node:readline";

const BASE = (process.env.PAPERCLIP_URL || "http://127.0.0.1:3100").replace(/\/+$/, "");
const MAX_CHARS = Number(process.env.PAPERCLIP_MCP_MAX_CHARS || 60000);
const BLOCKED = /secret|\/keys\b|token|login|auth|credential|password|\/access\b|\/env\b/i;

if (!/^https?:\/\/(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$/.test(BASE)) {
  process.stderr.write(`Refusing non-loopback PAPERCLIP_URL: ${BASE}\n`);
  process.exit(1);
}

const TOOLS = [
  {
    name: "paperclip_get",
    description:
      "GET a local Paperclip API path, e.g. /api/companies, /api/companies/{id}/issues, /api/issues/{id}, /api/heartbeat-runs/{id}. Read-only; credential paths are refused.",
    inputSchema: {
      type: "object",
      properties: {
        path: { type: "string", description: "Path starting with /api/." },
        query: { type: "object", description: "Optional query parameters.", additionalProperties: { type: ["string", "number", "boolean"] } },
      },
      required: ["path"],
    },
  },
  {
    name: "paperclip_find_endpoints",
    description: "Search the Paperclip OpenAPI document for GET endpoints whose path or summary matches a keyword.",
    inputSchema: {
      type: "object",
      properties: { search: { type: "string", description: "Case-insensitive keyword, e.g. issue, run, agent." } },
      required: ["search"],
    },
  },
];

async function get(path, query) {
  if (typeof path !== "string" || !path.startsWith("/api/") || path.includes("..")) throw new Error("path must start with /api/");
  if (BLOCKED.test(path)) throw new Error("Refused: path may expose credentials");
  const url = new URL(BASE + path);
  for (const [k, v] of Object.entries(query || {})) url.searchParams.set(k, String(v));
  const res = await fetch(url, { method: "GET", headers: { accept: "application/json" }, signal: AbortSignal.timeout(30000) });
  const text = await res.text();
  return { status: res.status, text };
}

let openapi;
async function findEndpoints(search) {
  openapi ??= JSON.parse((await get("/api/openapi.json")).text);
  const needle = String(search || "").toLowerCase();
  const hits = [];
  for (const [p, ops] of Object.entries(openapi.paths || {})) {
    const op = ops.get;
    if (!op || BLOCKED.test(p)) continue;
    const summary = op.summary || op.description || "";
    if (p.toLowerCase().includes(needle) || summary.toLowerCase().includes(needle)) hits.push(`GET ${p}${summary ? ` - ${summary.slice(0, 120)}` : ""}`);
  }
  return hits.length ? hits.slice(0, 80).join("\n") + (hits.length > 80 ? `\n... ${hits.length - 80} more` : "") : "No matching GET endpoints.";
}

async function callTool(name, args = {}) {
  if (name === "paperclip_find_endpoints") return { content: [{ type: "text", text: await findEndpoints(args.search) }] };
  if (name !== "paperclip_get") throw new Error(`Unknown tool: ${name}`);
  const { status, text } = await get(args.path, args.query);
  const body = text.length > MAX_CHARS ? text.slice(0, MAX_CHARS) + `\n... truncated ${text.length - MAX_CHARS} chars` : text;
  return { content: [{ type: "text", text: `HTTP ${status}\n${body}` }], isError: status >= 400 };
}

const send = (msg) => process.stdout.write(JSON.stringify(msg) + "\n");

createInterface({ input: process.stdin }).on("line", async (line) => {
  let req;
  try { req = JSON.parse(line); } catch { return; }
  const { id, method, params } = req;
  if (id === undefined) return; // notifications need no response

  try {
    let result;
    switch (method) {
      case "initialize":
        result = {
          protocolVersion: params?.protocolVersion ?? "2025-06-18",
          capabilities: { tools: {} },
          serverInfo: { name: "paperclip-readonly", version: "0.1.0" },
        };
        break;
      case "ping":
        result = {};
        break;
      case "tools/list":
        result = { tools: TOOLS };
        break;
      case "tools/call":
        result = await callTool(params?.name, params?.arguments);
        break;
      default:
        send({ jsonrpc: "2.0", id, error: { code: -32601, message: `Method not found: ${method}` } });
        return;
    }
    send({ jsonrpc: "2.0", id, result });
  } catch (e) {
    if (method === "tools/call") send({ jsonrpc: "2.0", id, result: { content: [{ type: "text", text: String(e.message ?? e) }], isError: true } });
    else send({ jsonrpc: "2.0", id, error: { code: -32603, message: String(e.message ?? e) } });
  }
});
