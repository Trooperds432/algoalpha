#!/usr/bin/env node
// Minimal stdio MCP server that exposes the local Codex CLI (`codex exec`) as tools.
// No dependencies: speaks newline-delimited JSON-RPC 2.0 per the MCP stdio transport.
//
// Guardrails: defaults to a read-only sandbox; never passes the bypass flags.
// "danger-full-access" is deliberately not offered.

import { spawn } from "node:child_process";
import { createInterface } from "node:readline";
import { resolve } from "node:path";

const CODEX_BIN = process.env.CODEX_BIN || "codex";
const DEFAULT_CWD = process.env.CODEX_MCP_CWD || process.cwd();
const TIMEOUT_MS = Number(process.env.CODEX_MCP_TIMEOUT_MS || 15 * 60 * 1000);
const SANDBOXES = ["read-only", "workspace-write"];

const TOOLS = [
  {
    name: "codex",
    description:
      "Start a new Codex session with a prompt and return its final message plus a session_id for follow-ups.",
    inputSchema: {
      type: "object",
      properties: {
        prompt: { type: "string", description: "Instructions for Codex." },
        sandbox: { type: "string", enum: SANDBOXES, default: "read-only" },
        cwd: { type: "string", description: "Working directory (defaults to the project root)." },
        model: { type: "string", description: "Optional model override." },
      },
      required: ["prompt"],
    },
  },
  {
    name: "codex_reply",
    description: "Continue an existing Codex session by session_id.",
    inputSchema: {
      type: "object",
      properties: {
        session_id: { type: "string" },
        prompt: { type: "string" },
        sandbox: { type: "string", enum: SANDBOXES, default: "read-only" },
        cwd: { type: "string" },
        model: { type: "string" },
      },
      required: ["session_id", "prompt"],
    },
  },
];

function runCodex({ prompt, sandbox = "read-only", cwd, model, sessionId }) {
  if (!SANDBOXES.includes(sandbox)) throw new Error(`sandbox must be one of ${SANDBOXES.join(", ")}`);
  const workdir = resolve(cwd || DEFAULT_CWD);
  const opts = ["--json", "--skip-git-repo-check", "--sandbox", sandbox, "--cd", workdir];
  if (model) opts.push("--model", model);
  // Prompt goes via stdin ("-") to avoid Windows command-line quoting issues.
  // `exec resume` rejects --sandbox/--cd, so they go on `exec` before the subcommand.
  const args = sessionId ? ["exec", ...opts, "resume", sessionId, "-"] : ["exec", ...opts, "-"];

  return new Promise((done, fail) => {
    const child = spawn(CODEX_BIN, args, { cwd: workdir, windowsHide: true, shell: false });
    let threadId = sessionId || null;
    const messages = [];
    const errors = [];
    let usage = null;
    let stderr = "";

    const timer = setTimeout(() => child.kill(), TIMEOUT_MS);
    createInterface({ input: child.stdout }).on("line", (line) => {
      let ev;
      try { ev = JSON.parse(line); } catch { return; }
      if (ev.type === "thread.started") threadId = ev.thread_id;
      else if (ev.type === "item.completed" && ev.item?.type === "agent_message") messages.push(ev.item.text);
      else if (ev.type === "turn.completed") usage = ev.usage ?? null;
      else if (ev.type === "error") errors.push(ev.message);
      else if (ev.type === "turn.failed") errors.push(ev.error?.message);
    });
    child.stderr.on("data", (d) => { stderr += d; });
    child.on("error", (e) => { clearTimeout(timer); fail(e); });
    child.on("close", (code) => {
      clearTimeout(timer);
      done({ code, threadId, messages, errors: [...new Set(errors.filter(Boolean))], usage, stderr: stderr.trim() });
    });
    child.stdin.end(prompt);
  });
}

async function callTool(name, args = {}) {
  if (name !== "codex" && name !== "codex_reply") throw new Error(`Unknown tool: ${name}`);
  if (!args.prompt) throw new Error("prompt is required");
  if (name === "codex_reply" && !args.session_id) throw new Error("session_id is required");

  const r = await runCodex({ ...args, sessionId: name === "codex_reply" ? args.session_id : undefined });
  const finalMessage = r.messages.at(-1) ?? "";
  const failed = r.code !== 0 || (r.errors.length > 0 && !finalMessage);
  const text = [
    finalMessage || "(no agent message)",
    "",
    `session_id: ${r.threadId ?? "unknown"}`,
    r.errors.length ? `errors: ${r.errors.join(" | ")}` : null,
    failed && r.stderr ? `stderr: ${r.stderr.slice(-2000)}` : null,
  ].filter((l) => l !== null).join("\n");
  return { content: [{ type: "text", text }], isError: failed };
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
          serverInfo: { name: "codex-cli-bridge", version: "0.1.0" },
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
