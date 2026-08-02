# Quality Evaluation Strategy

This project treats quality as layered evidence, not one flat test count.

## Methodology

- Test pyramid and size discipline: keep most checks deterministic and local, then reserve slower external checks for narrow smoke gates. See [Martin Fowler's Test Pyramid](https://martinfowler.com/bliki/TestPyramid.html) and [Google's small/medium/large test-size framing](https://testing.googleblog.com/2010/12/test-sizes.html).
- AI TEVV: document test, evaluation, verification, and validation evidence before deployment and keep monitoring after release. See [NIST AI TEVV](https://www.nist.gov/ai-test-evaluation-validation-and-verification-tevv).
- RAG evaluation: split retrieval quality from answer quality. Retrieval is measured with Recall@K and MRR@K; grounded generation is measured with citation/numeric validation and faithfulness-style checks. See the [Ragas metric catalog](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/).
- Regression evaluation: compare baseline and candidate systems on the same cases, then report paired deltas, confidence intervals, and effect size instead of a single pass/fail number. See [LangSmith evaluation concepts](https://docs.langchain.com/langsmith/evaluation-concepts?mode=ui) and [OpenAI Evals](https://github.com/openai/evals).

## Current Layers

Run: `python -m evaluation.test_inventory --report evaluation/reports/test-inventory-latest.json`

Current inventory after adding the layer report:

| Layer | Purpose | Tests |
|---|---|---:|
| L0 unit/contract | Parser, skills, answer builders, aliases, scorecards, small deterministic logic | 147 |
| L1 API/UI integration | FastAPI, SSE, structured UI flows, runtime events, multi-intent orchestration | 42 |
| L2 AI/RAG regression | 348-case golden set, retrieval benchmark, citation grounding, evidence synthesis | 429 |
| L3 resilience/security/ops | Fault injection, model/provider failures, Supercell boundaries, Redis quota, deployment and snapshot publication | 148 |
| L4 live external smoke | Credentialed live model/Supercell smoke checks, excluded from public CI | 0 in public unittest inventory |

The public inventory discovers `766` tests. The live smoke remains a separate command because it intentionally touches configured external systems.

## Quantitative Gates

- Deterministic contract regression: 344/344 enabled cases pass; 4 optional RAG-routing cases are skipped by design.
- Retrieval ablation on 80 snapshot RAG cases: BM25 baseline Recall@5 `0.9625`, MRR@5 `0.7556`; Hybrid Recall@5 `1.0000`, MRR@5 `0.9667`; Hybrid + rerank Recall@5 `1.0000`, MRR@5 `0.9875`.
- Grounding probes: 25/25 citation and numeric-grounding probes pass, invalid-citation rate `0`.
- Fault injection: 28/28 synthetic scenarios pass across grounding errors, model circuit behavior, quota/rate limit handling, stale snapshot/RAG alignment, stream fallback, and Supercell retry cooldown.
- Live RAG smoke: model parser, RAG synthesis, provider generation, and numeric/citation grounding pass on the local configured provider.

## Resume-Safe Wording

Built a layered AI evaluation gate: 766 discovered tests across unit/contract, API/SSE, AI/RAG regression, and resilience/security layers; 344/344 enabled golden cases and 28/28 fault-injection scenarios passed. Retrieval ablation on 80 RAG cases improved MRR@5 from 0.756 with BM25 to 0.988 with Hybrid + rerank.
