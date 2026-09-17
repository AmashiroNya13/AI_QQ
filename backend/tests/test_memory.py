import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ecobot.contracts import Stimulus
from ecobot.memory import MemoryStore


class MemoryStoreTests(unittest.TestCase):
    def test_source_identity_prevents_duplicate_memories(self) -> None:
        with TemporaryDirectory() as directory:
            store = MemoryStore(Path(directory) / "world.db")
            first = store.remember("likes tea", source_type="fact", source_id="1")
            second = store.remember("really likes tea", source_type="fact", source_id="1")
            self.assertEqual(first, second)
            result = store.retrieve(Stimulus("e", "c", "u", "tea", 1.0))
            self.assertEqual(result[-1]["content"], "really likes tea")
            store.close()

    def test_vector_similarity_can_rank_candidates(self) -> None:
        with TemporaryDirectory() as directory:
            store = MemoryStore(Path(directory) / "world.db")
            store.remember(
                "alpha", source_type="fact", source_id="1", embedding=[1.0, 0.0]
            )
            store.remember(
                "beta", source_type="fact", source_id="2", embedding=[0.0, 1.0]
            )
            result = store.retrieve(
                Stimulus("e", "c", "u", "", 1.0), query_embedding=[0.0, 1.0]
            )
            self.assertEqual(result[0]["content"], "beta")
            store.close()
