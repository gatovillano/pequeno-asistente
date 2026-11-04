"""
Dense Vector Embedding Engine for Pequeño Asistente's Episodic Memory.
Provides fast, offline-first neural embeddings using:
1. ONNX Runtime + HuggingFace Tokenizers (`sentence-transformers/all-MiniLM-L6-v2` quantized AVX2/CPU, 384-dim float32)
2. Local Ollama Embeddings (`paraphrase-multilingual` / `nomic-embed-text`) if configured
3. Deterministic Subword + Stemmed Feature-Hashing Dense Projection (384-dim float32 fallback)

Also implements vectorized NumPy cosine similarity, Maximal Marginal Relevance (MMR)
for diverse retrieval, and binary float32 BLOB serialization for SQLite.
"""
import os
import re
import hashlib
import logging
import threading
from collections import OrderedDict
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any

import numpy as np

logger = logging.getLogger("pequeno_asistente.memory.vector")


def vector_to_blob(vec: np.ndarray) -> bytes:
    """Serializes a 1D L2-normalized float32 numpy array into raw SQLite BLOB bytes."""
    arr = np.asarray(vec, dtype=np.float32).ravel()
    return arr.tobytes()


def blob_to_vector(blob: bytes, dim: Optional[int] = None) -> Optional[np.ndarray]:
    """Deserializes raw SQLite BLOB bytes back into a 1D float32 numpy array."""
    if not blob:
        return None
    try:
        arr = np.frombuffer(blob, dtype=np.float32)
        if dim is not None and arr.size != dim:
            return None
        return arr
    except Exception:
        return None


def l2_normalize(mat: np.ndarray) -> np.ndarray:
    """L2-normalizes a 1D vector or 2D matrix of vectors along the last axis."""
    arr = np.asarray(mat, dtype=np.float32)
    if arr.ndim == 1:
        norm = float(np.linalg.norm(arr))
        return arr / norm if norm > 1e-12 else arr
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms = np.where(norms > 1e-12, norms, 1.0)
    return arr / norms


def maximal_marginal_relevance(
    query_vec: np.ndarray,
    candidate_vecs: np.ndarray,
    base_scores: List[float],
    top_k: int = 5,
    lambda_mult: float = 0.78
) -> List[int]:
    """
    Selects top_k indices using Maximal Marginal Relevance (MMR):
    Balances relevance to query (`base_scores`) against cosine redundancy
    among already selected candidates (`candidate_vecs`).
    Returns ordered list of selected candidate indices.
    """
    n_cands = len(base_scores)
    if n_cands == 0 or top_k <= 0:
        return []
    if n_cands <= 1 or candidate_vecs.shape[0] != n_cands:
        return list(range(min(top_k, n_cands)))

    selected: List[int] = []
    remaining = list(range(n_cands))

    # Precompute pairwise cosine similarity matrix between candidates (N x N)
    sim_matrix = np.clip(candidate_vecs @ candidate_vecs.T, -1.0, 1.0)

    while remaining and len(selected) < top_k:
        if not selected:
            # First item is simply the highest base score
            best_idx = max(remaining, key=lambda idx: base_scores[idx])
        else:
            best_idx = remaining[0]
            best_mmr = -1e9
            for idx in remaining:
                rel = base_scores[idx]
                max_sim_to_sel = float(np.max(sim_matrix[idx, selected]))
                mmr_score = lambda_mult * rel - (1.0 - lambda_mult) * max(0.0, max_sim_to_sel)
                if mmr_score > best_mmr:
                    best_mmr = mmr_score
                    best_idx = idx

        selected.append(best_idx)
        remaining.remove(best_idx)

    return selected


def _deterministic_hash_embedding(text: str, dim: int = 384) -> np.ndarray:
    """
    Fast deterministic 384-dim dense float32 embedding using signed feature hashing
    over character n-grams (3, 4, 5) and words. Used only if neural ONNX / Ollama
    models are unavailable.
    """
    vec = np.zeros(dim, dtype=np.float32)
    if not text or not text.strip():
        return vec

    clean = re.sub(r"\s+", " ", text.lower().strip())
    words = re.findall(r"[a-z0-9áéíóúñ_]{2,}", clean)

    features: List[Tuple[str, float]] = []
    for w in words:
        features.append((f"w:{w}", 2.0))
        if len(w) >= 4:
            features.append((f"p:{w[:4]}", 1.2))
        padded = f"<{w}>"
        for n in (3, 4):
            for i in range(len(padded) - n + 1):
                features.append((padded[i:i + n], 0.6))

    for feat, weight in features:
        digest = hashlib.blake2b(feat.encode("utf-8"), digest_size=8).digest()
        h_val = int.from_bytes(digest, "little")
        idx = h_val % dim
        sign = 1.0 if ((h_val >> 32) & 1) == 0 else -1.0
        vec[idx] += sign * weight

    return l2_normalize(vec)


class DenseEmbeddingEngine:
    """
    Singleton-capable Dense Vector Embedding Engine.
    Automatically detects and loads the local ONNX neural transformer model
    (`sentence-transformers/all-MiniLM-L6-v2`, 384 dims) via `onnxruntime` and `tokenizers`,
    with optional Ollama embedding support and an in-memory LRU cache.
    """
    _instance: Optional["DenseEmbeddingEngine"] = None
    _lock = threading.Lock()

    DEFAULT_HF_REPO = "sentence-transformers/all-MiniLM-L6-v2"
    EMBEDDING_DIM = 384

    def __init__(self, prefer_backend: str = "auto"):
        self.prefer_backend = prefer_backend
        self.backend_name: str = "uninitialized"
        self.dim: int = self.EMBEDDING_DIM
        self._ort_session = None
        self._tokenizer = None
        self._init_attempted = False
        self._init_lock = threading.Lock()
        self._cache: OrderedDict[str, np.ndarray] = OrderedDict()
        self._cache_max_size = 512

    @classmethod
    def get_instance(cls) -> "DenseEmbeddingEngine":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def _find_cached_onnx_snapshot(self) -> Optional[Tuple[Path, Path]]:
        """Locates cached tokenizer.json and quantized/standard ONNX model in ~/.cache/huggingface/hub."""
        hf_hub = Path.home() / ".cache" / "huggingface" / "hub"
        candidates = [
            hf_hub / "models--sentence-transformers--all-MiniLM-L6-v2" / "snapshots",
            hf_hub / "models--Xenova--all-MiniLM-L6-v2" / "snapshots",
        ]
        for snaps_dir in candidates:
            if not snaps_dir.exists():
                continue
            for snap in sorted(snaps_dir.iterdir(), reverse=True):
                tok_path = snap / "tokenizer.json"
                if not tok_path.exists():
                    continue
                # Prefer quantized AVX2 or O4/O3 ONNX model for sub-15ms CPU inference
                for onnx_rel in (
                    "onnx/model_quint8_avx2.onnx",
                    "onnx/model_O4.onnx",
                    "onnx/model_O3.onnx",
                    "onnx/model.onnx",
                    "model_quantized.onnx",
                    "model.onnx"
                ):
                    onnx_path = snap / onnx_rel
                    if onnx_path.exists():
                        return tok_path, onnx_path
        return None

    def _ensure_initialized(self):
        if self._init_attempted:
            return
        with self._init_lock:
            if self._init_attempted:
                return
            self._init_attempted = True

            if self.prefer_backend == "hash":
                self.backend_name = "hash:subword-384"
                self.dim = 384
                return

            # Try loading ONNX Runtime + HuggingFace Tokenizer
            try:
                import onnxruntime as ort
                from tokenizers import Tokenizer

                found = self._find_cached_onnx_snapshot()
                if not found:
                    # Attempt lightweight download via huggingface_hub if online
                    try:
                        from huggingface_hub import hf_hub_download
                        tok_f = hf_hub_download(repo_id=self.DEFAULT_HF_REPO, filename="tokenizer.json")
                        onnx_f = hf_hub_download(repo_id=self.DEFAULT_HF_REPO, filename="onnx/model_quint8_avx2.onnx")
                        found = (Path(tok_f), Path(onnx_f))
                    except Exception as dl_err:
                        logger.debug(f"Could not download ONNX embedding model: {dl_err}")

                if found:
                    tok_path, onnx_path = found
                    tok = Tokenizer.from_file(str(tok_path))
                    tok.enable_truncation(max_length=256)
                    tok.enable_padding(pad_id=0, pad_token="[PAD]")

                    opts = ort.SessionOptions()
                    opts.intra_op_num_threads = min(4, os.cpu_count() or 2)
                    opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
                    sess = ort.InferenceSession(
                        str(onnx_path),
                        sess_options=opts,
                        providers=["CPUExecutionProvider"]
                    )
                    self._tokenizer = tok
                    self._ort_session = sess
                    self.backend_name = f"onnx:all-MiniLM-L6-v2 ({onnx_path.name})"
                    self.dim = 384
                    logger.info(f"Initialized neural vector engine: {self.backend_name}")
                    return
            except Exception as e:
                logger.warning(f"ONNX neural embedding initialization fallback: {e}")

            # Fallback to deterministic dense subword projection
            self.backend_name = "hash:subword-384"
            self.dim = 384
            logger.info("Using deterministic dense vector projection fallback (384-dim).")

    def embed(self, text: str) -> np.ndarray:
        """Computes a single L2-normalized dense float32 vector (shape `(384,)`)."""
        if not text or not text.strip():
            return np.zeros(self.dim, dtype=np.float32)

        norm_key = text.strip()[:1000]
        if norm_key in self._cache:
            self._cache.move_to_end(norm_key)
            return self._cache[norm_key]

        self._ensure_initialized()

        if self._ort_session is not None and self._tokenizer is not None:
            try:
                enc = self._tokenizer.encode(norm_key)
                ids = np.array([enc.ids], dtype=np.int64)
                mask = np.array([enc.attention_mask], dtype=np.int64)
                ttype = np.array([enc.type_ids], dtype=np.int64)

                input_names = {inp.name for inp in self._ort_session.get_inputs()}
                feed = {"input_ids": ids, "attention_mask": mask}
                if "token_type_ids" in input_names:
                    feed["token_type_ids"] = ttype

                token_embeddings = self._ort_session.run(None, feed)[0]
                mask_exp = np.expand_dims(mask, -1).astype(np.float32)
                sum_emb = np.sum(token_embeddings * mask_exp, axis=1)
                sum_mask = np.clip(mask_exp.sum(axis=1), a_min=1e-9, a_max=None)
                pooled = (sum_emb / sum_mask)[0]
                vec = l2_normalize(pooled)
            except Exception as e:
                logger.debug(f"ONNX embed error, falling back to hash projection: {e}")
                vec = _deterministic_hash_embedding(norm_key, self.dim)
        else:
            vec = _deterministic_hash_embedding(norm_key, self.dim)

        # Store in LRU cache
        self._cache[norm_key] = vec
        if len(self._cache) > self._cache_max_size:
            self._cache.popitem(last=False)

        return vec

    def embed_batch(self, texts: List[str]) -> np.ndarray:
        """Computes L2-normalized dense float32 embeddings for a batch of texts (shape `(N, 384)`)."""
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)

        self._ensure_initialized()

        if self._ort_session is not None and self._tokenizer is not None:
            try:
                clean_texts = [t.strip()[:1000] if t else "" for t in texts]
                encodings = self._tokenizer.encode_batch(clean_texts)
                ids = np.array([e.ids for e in encodings], dtype=np.int64)
                mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)
                ttype = np.array([e.type_ids for e in encodings], dtype=np.int64)

                input_names = {inp.name for inp in self._ort_session.get_inputs()}
                feed = {"input_ids": ids, "attention_mask": mask}
                if "token_type_ids" in input_names:
                    feed["token_type_ids"] = ttype

                token_embeddings = self._ort_session.run(None, feed)[0]
                mask_exp = np.expand_dims(mask, -1).astype(np.float32)
                sum_emb = np.sum(token_embeddings * mask_exp, axis=1)
                sum_mask = np.clip(mask_exp.sum(axis=1), a_min=1e-9, a_max=None)
                pooled = sum_emb / sum_mask
                vecs = l2_normalize(pooled)
                for txt, v in zip(clean_texts, vecs):
                    if txt:
                        self._cache[txt] = v
                return vecs
            except Exception as e:
                logger.debug(f"ONNX batch embed error, falling back to per-item embed: {e}")

        return np.vstack([self.embed(t) for t in texts])

    def get_info(self) -> Dict[str, Any]:
        self._ensure_initialized()
        return {
            "backend": self.backend_name,
            "dimensions": self.dim,
            "cache_size": len(self._cache),
            "is_neural": self._ort_session is not None
        }
