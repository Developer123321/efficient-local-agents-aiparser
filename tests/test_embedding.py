import math
from pathlib import Path

import pytest

from hacklab import agent, embedding
from hacklab.config import Config
from hacklab.embedding import EmbeddingError, OnnxEmbedder, cosine, fuse
from hacklab.evidence import Ledger
from hacklab.toolkit import Toolbox
from hacklab.tools import ToolRegistry

MODEL = Path(__file__).parents[1] / ".state" / "models" / "embeddinggemma-300m-onnx"
SYNONYMS = {"hijacked": "compromised", "cancelled": "revoke", "credentials": "token"}


class FakeEmbedder:
    """Bag of words over a tiny vocabulary with synonyms: enough to test the plumbing."""

    vocabulary = ("compromised", "revoke", "token", "payload", "lunch", "quarantine")

    def __init__(self):
        self.calls = []

    def encode(self, texts, query=False):
        self.calls.append((list(texts), query))
        vectors = []
        for text in texts:
            words = [SYNONYMS.get(word, word) for word in text.lower().replace("?", "").split()]
            vectors.append([float(sum(term in word for word in words)) for term in self.vocabulary])
        return vectors


@pytest.fixture
def documents(tmp_path):
    (tmp_path / "runbook.md").write_text(
        "An agent is compromised once it runs a staged payload.\n"
        "Revoke every token the agent created.\n"
        "Lunch is at noon.\n"
    )
    (tmp_path / "audit.jsonl").write_text(
        '{"ts":"2026-11-14T01:00:00Z","actor":"a1","action":"hijacked note"}\n' * 3
    )
    return ToolRegistry(tmp_path, ["runbook.md", "audit.jsonl"])


def box(documents, embedder=None, **settings):
    config = Config()
    for key, value in settings.items():
        setattr(config.embedding, key, value)
    return Toolbox(documents, Ledger(config.guard), config, embedder=embedder)


def test_rank_fusion_and_cosine():
    assert (
        cosine([1, 0], [1, 0]) == 1 and cosine([1, 0], [0, 1]) == 0 and cosine([0, 0], [1, 1]) == 0
    )
    assert math.isclose(cosine([1, 1], [1, 0]), 1 / math.sqrt(2))
    # What both rankings put last stays last; with weight 1 only the semantic
    # order counts, with 0 only the lexical.
    assert fuse([0, 1, 2], [1, 0, 2])[-1] == 2
    assert fuse([0, 1, 2], [2, 0, 1], weight=1.0) == [2, 0, 1]
    assert fuse([0, 1, 2], [2, 0, 1], weight=0.0) == [0, 1, 2]
    assert fuse([0], [3, 0]) == [0, 3]


def test_a_paraphrase_without_a_shared_word_is_found_only_with_the_embedder(documents):
    query = "Which credentials must be cancelled?"
    keyword = box(documents).search_rows(query, 2)["results"]
    assert not any("Revoke" in row["quote"] for row in keyword)
    embedder = FakeEmbedder()
    toolbox = box(documents, embedder)
    found = toolbox.search_rows(query, 2)["results"]
    assert found[0] == {
        "document": "runbook.md",
        "locator": "line:2",
        "quote": "Revoke every token the agent created.",
    }
    assert embedder.calls[-1] == ([query], True)
    # A document line is embedded once per task, however many searches rank it.
    toolbox.search_rows("When is an agent hijacked?", 2)
    toolbox.search_rows(query, 2)
    encoded = [text for texts, is_query in embedder.calls if not is_query for text in texts]
    assert len(encoded) == len(set(encoded)) and "Lunch is at noon." in encoded
    shown = toolbox.execute("search", {"query": "When is an agent hijacked?", "limit": 1})
    assert shown.lines[0] == (
        "[e1] runbook.md line:1 | An agent is compromised once it runs a staged payload."
    )


def test_a_failing_embedder_leaves_the_keyword_ranking(documents):
    class Broken:
        def encode(self, texts, query=False):
            raise RuntimeError("model file damaged")

    plain = box(documents).search_rows("staged payload", 2)["results"]
    assert box(documents, Broken()).search_rows("staged payload", 2)["results"] == plain
    assert "error" in box(documents, FakeEmbedder()).search_rows("", 2)


def test_missing_model_or_runtime_is_reported_and_the_task_still_runs(tmp_path, monkeypatch):
    with pytest.raises(EmbeddingError):
        OnnxEmbedder(tmp_path / "absent")
    (tmp_path / "note.txt").write_text("Warehouse opens at 09:00.\n")
    from hacklab.models import Task
    from hacklab.tracking import Tracker

    config = Config()
    config.embedding.enabled = True
    config.embedding.model_dir = str(tmp_path / "absent")
    agent._embedder.cache_clear()
    tracker = Tracker(tmp_path / "state" / "t.sqlite")
    task = Task(id="one", question="Return the source line.", documents=["note.txt"])
    _, answer = agent.run_task(task, tmp_path, config, tracker)
    assert answer.status == "ok" and answer.answer == "Warehouse opens at 09:00."


def test_the_embedder_is_off_in_every_shipped_profile():
    from hacklab.config import load_config

    for path in (Path(__file__).parents[1] / "configs").glob("*.toml"):
        assert load_config(path).embedding.enabled is False, path.name


def test_fetch_names_only_the_ungated_export_and_the_files_the_runtime_needs():
    assert embedding.REPOSITORY == "onnx-community/embeddinggemma-300m-ONNX"
    assert embedding.FILES == (
        "tokenizer.json",
        "onnx/model_quantized.onnx",
        "onnx/model_quantized.onnx_data",
    )


@pytest.mark.skipif(not (MODEL / "tokenizer.json").is_file(), reason="model not downloaded")
def test_real_embeddinggemma_ranks_a_paraphrase():
    pytest.importorskip("onnxruntime")
    model = OnnxEmbedder(MODEL)
    lines = ["Revoke every token the agent created.", "Lunch is at noon.", "The hub lists models."]
    query = model.encode(["Which credentials have to be cancelled?"], query=True)[0]
    vectors = model.encode(lines)
    assert len(query) == 768
    assert max(range(3), key=lambda index: cosine(query, vectors[index])) == 0
