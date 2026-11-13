/** Synthetic API throughput only. This does not measure model quality or model cost. */
import { Database } from "bun:sqlite";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createApi, TelemetryStore } from "./api";

const RUNS = 10_000;
const CALLS_PER_RUN = 10;
const directory = mkdtempSync(join(tmpdir(), "hacklab-dashboard-bench-"));
const path = join(directory, "synthetic.sqlite");
const writer = new Database(path);
writer.exec(`PRAGMA journal_mode=WAL;
  CREATE TABLE runs(id TEXT PRIMARY KEY,task_id TEXT,started_at TEXT,finished_at TEXT,status TEXT,
    mode TEXT,config_hash TEXT,config_json TEXT,metadata_json TEXT,answer_hash TEXT,score REAL);
  CREATE TABLE calls(id INTEGER PRIMARY KEY,run_id TEXT,created_at TEXT,provider TEXT,model TEXT,
    input_tokens INTEGER,output_tokens INTEGER,latency_ms REAL,status TEXT,input_rate REAL,
    output_rate REAL,cost_usd REAL,billable INTEGER,metadata_json TEXT);
  CREATE TABLE events(id INTEGER PRIMARY KEY,run_id TEXT,created_at TEXT,kind TEXT,data_json TEXT);
  CREATE INDEX calls_run ON calls(run_id);
  CREATE INDEX runs_started ON runs(started_at DESC,id DESC);`);
const insertRun = writer.query(
  "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
);
const insertCall = writer.query(
  "INSERT INTO calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
);
writer.transaction(() => {
  for (let index = 0; index < RUNS; index++) {
    const id = `synthetic-${String(index).padStart(5, "0")}`;
    const day = String((index % 14) + 1).padStart(2, "0");
    const created = `2026-11-${day}T00:00:00Z`;
    insertRun.run(
      id,
      id,
      created,
      created,
      "ok",
      "openai",
      "synthetic-digest",
      '{"model":{"model":"synthetic-model"},"agent":{"workflow":"pipeline"}}',
      "{}",
      "synthetic-answer-digest",
      index % 5 === 0 ? 0 : 1,
    );
    for (let call = 0; call < CALLS_PER_RUN; call++) {
      insertCall.run(
        index * CALLS_PER_RUN + call + 1,
        id,
        created,
        "openai",
        "synthetic-model",
        100,
        50,
        25,
        "ok",
        0.1,
        0.2,
        index % 20 === 0 ? null : 0.00002,
        1,
        JSON.stringify({ stage: call % 2 ? "responder" : "analyst" }),
      );
    }
  }
})();
writer.close();
const store = new TelemetryStore(path);
const api = createApi(store);
const server = Bun.serve({
  hostname: "127.0.0.1",
  port: 0,
  fetch: (request) =>
    api(request) ?? new Response("Not found", { status: 404 }),
});
function percentile(sorted: number[], proportion: number): number {
  return sorted[Math.max(0, Math.ceil(sorted.length * proportion) - 1)]!;
}
async function request(endpoint: string) {
  const start = performance.now();
  const response = await fetch(`http://127.0.0.1:${server.port}${endpoint}`);
  const body = await response.arrayBuffer();
  if (!response.ok) throw new Error(`synthetic_api_http_${response.status}`);
  return { elapsedMs: performance.now() - start, bytes: body.byteLength };
}
try {
  const measurements = [];
  for (const endpoint of [
    "/api/overview",
    "/api/runs?limit=50&offset=5000",
    "/api/campaigns",
  ]) {
    const cold = await request(endpoint);
    for (let warmup = 0; warmup < 10; warmup++) await request(endpoint);
    const samples = [];
    let bytes = 0;
    for (let sample = 0; sample < 200; sample++) {
      const measured = await request(endpoint);
      samples.push(measured.elapsedMs);
      bytes = measured.bytes;
    }
    samples.sort((a, b) => a - b);
    measurements.push({
      endpoint,
      samples: samples.length,
      coldMs: cold.elapsedMs,
      p50Ms: percentile(samples, 0.5),
      p95Ms: percentile(samples, 0.95),
      responseBytes: bytes,
    });
  }
  console.log(
    JSON.stringify(
      {
        scope: "synthetic local API; no model requests",
        bun: Bun.version,
        runs: RUNS,
        calls: RUNS * CALLS_PER_RUN,
        aggregateCacheMs: 2000,
        measurements,
      },
      null,
      2,
    ),
  );
} finally {
  await server.stop(true);
  store.close();
  rmSync(directory, { recursive: true, force: true });
}
