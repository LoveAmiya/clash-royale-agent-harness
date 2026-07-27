import { checkReadiness, processStructuredQuestion, summaryOutput } from "./common.js";

export const options = {
  scenarios: {
    sustained_process_load: {
      executor: "ramping-vus",
      startVUs: 0,
      stages: [
        { duration: __ENV.RAMP_UP || "15s", target: Number(__ENV.TARGET_VUS || "4") },
        { duration: __ENV.HOLD_FOR || "60s", target: Number(__ENV.TARGET_VUS || "4") },
        { duration: __ENV.RAMP_DOWN || "15s", target: 0 },
      ],
      gracefulRampDown: "10s",
    },
  },
  thresholds: {
    checks: ["rate>0.99"],
    http_req_failed: ["rate<0.01"],
    process_failures: ["rate<0.01"],
    process_duration: ["p(95)<5000", "p(99)<10000"],
    process_ttfb: ["p(95)<2000"],
  },
};

export function setup() {
  checkReadiness();
}

export default function () {
  processStructuredQuestion();
}

export function handleSummary(data) {
  return summaryOutput(data, "load-summary");
}

