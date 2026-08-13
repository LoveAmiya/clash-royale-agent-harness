# ADR-014: Bounded Multi-stage RAG Retrieval

## Status

Accepted

## Date

2026-08-11

## Context

The rolling corpus currently contains tens of thousands of high-density evidence
documents and may grow to hundreds of thousands. Retrieval must improve recall
without sending a large candidate set to the synthesis model or loading an
additional heavyweight reranker by default.

The previous implementation normalized BM25 and dense scores independently and
combined them with a weighted sum. That is sensitive to each result set's score
distribution and gave the reranker only a small candidate pool.

Relevant upstream designs:

- Qdrant hybrid queries support staged prefetch and reciprocal-rank fusion:
  https://qdrant.tech/documentation/concepts/hybrid-queries/
- Sentence Transformers documents retrieve-then-CrossEncoder-rerank pipelines:
  https://www.sbert.net/examples/sentence_transformer/applications/retrieve_rerank/README.html
- FlagEmbedding provides BGE rerankers and late-interaction models:
  https://github.com/FlagOpen/FlagEmbedding
- ColBERT provides scalable late-interaction retrieval:
  https://github.com/stanford-futuredata/ColBERT

## Decision

Use a bounded multi-stage pipeline:

1. Hard-filter retrieval by the selected dataset scope and entity/deck mode.
2. Recall up to 32 BM25 and 32 dense candidates per lane.
3. Fuse ranks with weighted reciprocal-rank fusion using k=60.
4. Keep at most 24 global candidates and 8 candidates per typed meta lane.
5. Apply the existing deterministic metadata and lexical reranker.
6. Preserve source-type diversity and keep at most 12 meta evidence candidates.
7. Compress at most 10 evidence items within a 4,200-character context budget.

All limits are bounded at configuration load time. Rolling scopes continue to
use lazy BM25 indexes with a two-scope cache. The response trace exposes only
operational diagnostics: fusion mode, lane names, candidate counts, reranked
count, evidence count, and context budget. It does not expose hidden model
reasoning, prompts, credentials, or raw evidence payloads.

The legacy weighted fusion remains available through
`RETRIEVAL_FUSION_MODE=weighted` for rollback.

## Consequences

- Candidate recall grows while model context remains bounded.
- Documents found by both sparse and dense retrieval receive a stable rank
  advantage without comparing incompatible raw score scales.
- The selected scope's RAG document count is published in the rolling manifest.
  Older manifests are counted once and cached in process.
- The browser status cards follow the selected rolling dataset and refresh after
  an answer instead of remaining tied to the legacy snapshot view.
- No new Python or model dependency is required for the default path.

## Deferred Scale Step

Before a single active scope approaches roughly 100,000 documents, benchmark
recall, MRR, p95 latency, and peak RSS with the production query set. If the
deterministic reranker plateaus, add an optional local BGE CrossEncoder reranker
behind a feature flag. If embedded Qdrant or local BM25 becomes the measured
bottleneck, migrate sparse and dense vectors to Qdrant server mode and use
native prefetch plus RRF. ColBERT-style late interaction is reserved for a
separate benchmark because it adds index size, model memory, and deployment
complexity.

## Verification

- Unit tests cover RRF rank fusion, scope filtering, lane attribution, and
  bounded candidate diagnostics.
- Materialization tests verify that per-scope RAG counts sum to the global
  document count.
- Frontend contract tests verify selected-scope status and visible environment
  analysis traces.
- Existing retrieval benchmark and full regression suites remain required
  before activation.
