"""本地混合检索：词法 BM25 加语义向量相似度。

BM25 与稠密向量分数不在同一量纲，因此本实现先归一化各自候选列表，再做可配置的
加权融合。结果保留两路分数，便于调试和离线评测。
"""

import json
import math
from pathlib import Path
from typing import List, Dict, Any, Optional

import requests
from rank_bm25 import BM25Okapi
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct

from app_config import EMBED_MODEL, OLLAMA_EMBED_URL, RAG_DOCS_FILE


DATA_DIR = Path("data")
DOCS_FILE = RAG_DOCS_FILE

COLLECTION_NAME = "cr_hybrid_rag"
VECTOR_SIZE = 1024


class HybridRetriever:
    """构建内存中的 BM25/Qdrant 索引，并提供可解释的检索结果。"""
    def __init__(self, docs: List[Dict[str, Any]]):
        """对同一批文档建立两套索引，使词法与语义召回使用相同语料。"""
        self.docs = docs
        self.doc_id_to_doc = {}

        self.tokenized_corpus = [self.tokenize(doc["text"]) for doc in docs]
        self.bm25 = BM25Okapi(self.tokenized_corpus)

        self.qdrant = QdrantClient(":memory:")
        self._build_dense_index()

    @staticmethod
    def tokenize(text: str) -> List[str]:
        """为 BM25 切分英文单词和单个中文字符。"""
        text = text.lower()
        english_tokens = []
        current = []

        for ch in text:
            if ch.isalnum() or ch in ["-", "_", "."]:
                current.append(ch)
            else:
                if current:
                    english_tokens.append("".join(current))
                    current = []

        if current:
            english_tokens.append("".join(current))

        chinese_tokens = [ch for ch in text if "\u4e00" <= ch <= "\u9fff"]

        return english_tokens + chinese_tokens

    def embed_text(self, text: str) -> List[float]:
        """调用配置好的 Ollama embedding 服务，并校验返回契约。

        在此校验向量长度，可以在模型与 Qdrant collection schema 不匹配时尽早失败。
        """
        payload = {
            "model": EMBED_MODEL,
            "input": text,
        }
        try:
            resp = requests.post(OLLAMA_EMBED_URL, json=payload, timeout=60)
        except requests.RequestException as exc:
            raise RuntimeError(
                f"无法连接到 Ollama embedding 服务：{OLLAMA_EMBED_URL}。"
                f"请确认已启动 ollama，并已准备模型 {EMBED_MODEL}。"
            ) from exc

        resp.raise_for_status()
        data = resp.json()

        embeddings = data.get("embeddings")
        if not embeddings or not isinstance(embeddings, list):
            raise ValueError("Ollama embedding 返回格式异常。")

        vector = embeddings[0]
        if len(vector) != VECTOR_SIZE:
            raise ValueError(f"向量维度异常，期望 {VECTOR_SIZE}，实际 {len(vector)}。")

        return vector

    def _build_dense_index(self):
        """为当前文档语料创建可丢弃的本地 Qdrant collection。"""
        if self.qdrant.collection_exists(COLLECTION_NAME):
            self.qdrant.delete_collection(COLLECTION_NAME)

        self.qdrant.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
        )

        points = []
        for idx, doc in enumerate(self.docs):
            self.doc_id_to_doc[idx] = doc
            try:
                vector = self.embed_text(doc["text"])
            except Exception as exc:
                raise RuntimeError(
                    f"构建向量索引失败，文档 doc_id={doc.get('doc_id')}。"
                    "请检查 Ollama 服务、embedding 模型和网络连通性。"
                ) from exc

            payload = {
                "doc_id": doc["doc_id"],
                "source_type": doc["source_type"],
                "text": doc["text"],
                "metadata": doc["metadata"],
            }

            points.append(
                PointStruct(
                    id=idx,
                    vector=vector,
                    payload=payload,
                )
            )

        self.qdrant.upsert(collection_name=COLLECTION_NAME, points=points)

    def bm25_search(self, query: str, top_k: int = 10, source_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """返回按词频相关性排序的词法候选文档。"""
        tokenized_query = self.tokenize(query)
        scores = self.bm25.get_scores(tokenized_query)

        indexed_scores = list(enumerate(scores))
        indexed_scores.sort(key=lambda x: x[1], reverse=True)

        results = []
        for idx, score in indexed_scores:
            doc = self.doc_id_to_doc[idx]
            if source_type is not None and doc["source_type"] != source_type:
                continue

            results.append(
                {
                    "internal_id": idx,
                    "score": float(score),
                    "doc": doc,
                }
            )
            if len(results) >= top_k:
                break

        return results

    def dense_search(self, query: str, top_k: int = 10, source_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """返回向量索引中按余弦相似度排序的语义候选文档。"""
        query_vector = self.embed_text(query)

        response = self.qdrant.query_points(
            collection_name=COLLECTION_NAME,
            query=query_vector,
            limit=max(top_k * 3, 20),
            with_payload=True,
        )

        points = response.points if hasattr(response, "points") else []

        results = []
        for item in points:
            internal_id = item.id
            doc = self.doc_id_to_doc[internal_id]

            if source_type is not None and doc["source_type"] != source_type:
                continue

            results.append(
                {
                    "internal_id": internal_id,
                    "score": float(item.score),
                    "doc": doc,
                }
            )
            if len(results) >= top_k:
                break

        return results

    @staticmethod
    def normalize_scores(results: List[Dict[str, Any]]) -> Dict[int, float]:
        """在跨检索器融合前，将一路检索分数映射到 0..1。"""
        if not results:
            return {}

        scores = [r["score"] for r in results]
        min_score = min(scores)
        max_score = max(scores)

        if math.isclose(min_score, max_score):
            return {r["internal_id"]: 1.0 for r in results}

        norm = {}
        for r in results:
            norm[r["internal_id"]] = (r["score"] - min_score) / (max_score - min_score)
        return norm

    def hybrid_search(
        self,
        query: str,
        top_k_bm25: int = 10,
        top_k_dense: int = 10,
        final_top_k: int = 5,
        alpha: float = 0.5,
        source_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """以可审计的加权分数融合词法与稠密候选集。

        ``alpha`` 控制词法贡献。只被一路召回的文档仍会保留，缺失一路记为 0 分；
        这样既提升召回，也能解释它为什么排在当前位置。
        """
        bm25_results = self.bm25_search(query, top_k=top_k_bm25, source_type=source_type)
        dense_results = self.dense_search(query, top_k=top_k_dense, source_type=source_type)

        bm25_norm = self.normalize_scores(bm25_results)
        dense_norm = self.normalize_scores(dense_results)

        merged = {}

        for r in bm25_results:
            idx = r["internal_id"]
            merged.setdefault(idx, {"doc": r["doc"], "bm25_score": 0.0, "dense_score": 0.0})
            merged[idx]["bm25_score"] = bm25_norm.get(idx, 0.0)

        for r in dense_results:
            idx = r["internal_id"]
            merged.setdefault(idx, {"doc": r["doc"], "bm25_score": 0.0, "dense_score": 0.0})
            merged[idx]["dense_score"] = dense_norm.get(idx, 0.0)

        final_results = []
        for idx, item in merged.items():
            final_score = alpha * item["bm25_score"] + (1 - alpha) * item["dense_score"]
            final_results.append(
                {
                    "internal_id": idx,
                    "final_score": final_score,
                    "bm25_score": item["bm25_score"],
                    "dense_score": item["dense_score"],
                    "doc": item["doc"],
                }
            )

        final_results.sort(key=lambda x: x["final_score"], reverse=True)
        return final_results[:final_top_k]


def load_docs():
    if not DOCS_FILE.exists():
        raise FileNotFoundError(f"没有找到 {DOCS_FILE}，请先执行 py rag_data_builder.py")
    with open(DOCS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def demo():
    docs = load_docs()
    retriever = HybridRetriever(docs)

    query = input("请输入测试问题：").strip()
    source_type = input("请输入检索类型(card / deck / schedule，可直接回车跳过)：").strip().lower()

    if source_type == "":
        source_type = None

    results = retriever.hybrid_search(
        query=query,
        top_k_bm25=10,
        top_k_dense=10,
        final_top_k=5,
        alpha=0.5,
        source_type=source_type,
    )

    print("\n===== Hybrid Retrieval Top Results =====\n")
    for i, item in enumerate(results, start=1):
        doc = item["doc"]
        print(f"[{i}] source_type = {doc['source_type']}")
        print(f"doc_id = {doc['doc_id']}")
        print(f"final_score = {item['final_score']:.4f}")
        print(f"bm25_score = {item['bm25_score']:.4f}")
        print(f"dense_score = {item['dense_score']:.4f}")
        print(f"text = {doc['text']}")
        print("-" * 80)


if __name__ == "__main__":
    demo()
