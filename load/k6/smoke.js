import { checkReadiness, processStructuredQuestion, summaryOutput } from "./common.js";

export const options = {
  vus: 1,
  iterations: 3,
  thresholds: {
    checks: ["rate==1"],
    process_failures: ["rate==0"],
    process_duration: ["p(95)<5000"],
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
  return summaryOutput(data, "smoke-summary");
}

