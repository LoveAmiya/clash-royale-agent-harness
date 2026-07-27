import { checkReadiness, processStructuredQuestion, summaryOutput } from "./common.js";

export const options = {
  scenarios: {
    process_soak: {
      executor: "constant-vus",
      vus: Number(__ENV.SOAK_VUS || "2"),
      duration: __ENV.SOAK_DURATION || "30m",
      gracefulStop: "30s",
    },
  },
  thresholds: {
    checks: ["rate>0.995"],
    http_req_failed: ["rate<0.005"],
    process_failures: ["rate<0.005"],
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
  return summaryOutput(data, "soak-summary");
}

