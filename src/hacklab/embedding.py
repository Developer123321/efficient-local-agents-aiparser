"""Optional semantic ranking with EmbeddingGemma, run locally without a network.

OFF by default. The event's rules forbid an own embedder in the agent unless
the organizers provide one: enable `[embedding]` only when this model is among
the provided ones or the organizers have said that it is allowed.

The model runs through ONNX Runtime from a local directory, so nothing is
fetched at run time. `fetch` downloads the files once; it is the only code
here that touches the network and it runs only on an explicit command.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

# The official ONNX export. The original repository google/embeddinggemma-300m
# is gated behind a licence click and needs the user's own Hugging Face login.
REPOSITORY = "onnx-community/embeddinggemma-300m-ONNX"
FILES = ("tokenizer.json", "onnx/model_quantized.onnx", "onnx/model_quantized.onnx_data")
# Prompts from the model card: queries and documents are embedded differently.
QUERY_PREFIX = "task: search result | query: "
DOCUMENT_PREFIX = "title: none | text: "
MAX_FILE_BYTES = 2_000_000_000


class EmbeddingError(RuntimeError):
    """The embedder is missing, incomplete or not installable here."""


class Embedder(Protocol):
    def encode(self, texts: Sequence[str], query: bool = False) -> list[list[float]]: ...


def cosine(left: Sequence[float], right: Sequence[float]) -> float:
    norm = math.sqrt(sum(x * x for x in left)) * math.sqrt(sum(y * y for y in right))
    return sum(x * y for x, y in zip(left, right, strict=True)) / norm if norm else 0.0


def fuse(lexical: Sequence[int], semantic: Sequence[int], weight: float = 0.5) -> list[int]:
    """Reciprocal rank fusion of two rankings of the same candidate indices.

    `weight` is the share of the semantic ranking. Rank fusion needs no score
    calibration between BM25 and cosine similarity.
    """
    scores: dict[int, float] = {}
    for share, ranking in ((1 - weight, lexical), (weight, semantic)):
        for rank, index in enumerate(ranking):
            scores[index] = scores.get(index, 0.0) + share / (60 + rank)
    return sorted(scores, key=lambda index: (-scores[index], index))


class OnnxEmbedder:
    """EmbeddingGemma from a local directory holding tokenizer.json and onnx/."""

    def __init__(self, model_dir: Path, max_tokens: int = 256, batch: int = 16):
        try:
            import numpy
            import onnxruntime
            from tokenizers import Tokenizer
        except ImportError as exc:
            raise EmbeddingError(
                "Install the embeddings extra: uv sync --extra embeddings (Python 3.11 to 3.14)"
            ) from exc
        directory = Path(model_dir)
        missing = [name for name in FILES if not (directory / name).is_file()]
        if missing:
            raise EmbeddingError(f"Embedding model files missing in {directory}: {missing[0]}")
        self._numpy = numpy
        self._tokenizer = Tokenizer.from_file(str(directory / FILES[0]))
        self._tokenizer.enable_truncation(max_length=max_tokens)
        self._tokenizer.enable_padding()
        options = onnxruntime.SessionOptions()
        options.log_severity_level = 3
        self._session = onnxruntime.InferenceSession(
            str(directory / FILES[1]), options, providers=["CPUExecutionProvider"]
        )
        self._inputs = {item.name for item in self._session.get_inputs()}
        self._batch = batch

    def encode(self, texts: Sequence[str], query: bool = False) -> list[list[float]]:
        prefix = QUERY_PREFIX if query else DOCUMENT_PREFIX
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch):
            encoded = self._tokenizer.encode_batch(
                [prefix + text for text in texts[start : start + self._batch]]
            )
            feeds: dict[str, Any] = {
                "input_ids": self._numpy.array([item.ids for item in encoded], dtype="int64"),
                "attention_mask": self._numpy.array(
                    [item.attention_mask for item in encoded], dtype="int64"
                ),
            }
            outputs = self._session.run(
                ["sentence_embedding"], {k: v for k, v in feeds.items() if k in self._inputs}
            )
            vectors.extend(outputs[0].tolist())
        return vectors


def fetch(model_dir: Path) -> dict[str, str]:
    """Download the model files once and return their SHA-256 digests."""
    import httpx

    directory = Path(model_dir)
    digests = {}
    for name in FILES:
        target = directory / name
        target.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        url = f"https://huggingface.co/{REPOSITORY}/resolve/main/{name}"
        partial = target.with_suffix(target.suffix + ".part")
        with httpx.stream("GET", url, follow_redirects=True, timeout=120) as response:
            response.raise_for_status()
            size = 0
            with partial.open("wb") as stream:
                for chunk in response.iter_bytes(1 << 20):
                    size += len(chunk)
                    if size > MAX_FILE_BYTES:
                        raise EmbeddingError("Embedding model file exceeds 2 GB")
                    digest.update(chunk)
                    stream.write(chunk)
        partial.replace(target)
        digests[name] = digest.hexdigest()
    return digests
