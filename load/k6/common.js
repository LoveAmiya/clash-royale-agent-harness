import http from "k6/http";
import { check, sleep } from "k6";
import { Rate, Trend } from "k6/metrics";

export const processFailures = new Rate("process_failures");
export const processDuration = new Trend("process_duration", true);
export const processTtfb = new Trend("process_ttfb", true);

const baseUrl = (__ENV.BASE_URL || "http://proxy").replace(/\/$/, "");
const question = __ENV.TEST_QUESTION || "Electro Giant usage rate and win rate";

export function checkReadiness() {
  const response = http.get(`${baseUrl}/ready`, { tags: { endpoint: "ready" } });
  check(response, {
    "ready endpoint responds": (item) => item.status === 200,
    "ready payload is operational": (item) => ["ready", "degraded"].includes(item.json("status")),
  });
}

export function processStructuredQuestion() {
  const payload = JSON.stringify({
    session_id: `k6-${__VU}-${__ITER}`,
    input: [{ role: "user", content: [{ type: "text", text: question }] }],
  });
  const response = http.post(`${baseUrl}/process`, payload, {
    headers: { "Content-Type": "application/json", "X-Request-ID": `k6-${__VU}-${__ITER}` },
    tags: { endpoint: "process" },
    timeout: __ENV.PROCESS_TIMEOUT || "30s",
  });
  const valid = check(response, {
    "process returns SSE": (item) => item.status === 200 && item.headers["Content-Type"].includes("text/event-stream"),
    "process emits content": (item) => item.body.includes('"object": "content"'),
    "process completes trace": (item) => item.body.includes('"object": "trace"') && item.body.includes('"status": "completed"'),
  });
  processFailures.add(!valid);
  processDuration.add(response.timings.duration);
  processTtfb.add(response.timings.waiting);
  sleep(Number(__ENV.ITERATION_PAUSE_SECONDS || "0.2"));
}

export function summaryOutput(data, defaultName) {
  const outputPath = __ENV.SUMMARY_PATH || `/reports/${defaultName}.json`;
  return { [outputPath]: JSON.stringify(data, null, 2) };
}

