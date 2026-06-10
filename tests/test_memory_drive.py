from __future__ import annotations

import json
import sqlite3
import sys
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock


def _install_external_stubs() -> None:
    if "httpx" not in sys.modules:
        httpx = types.ModuleType("httpx")

        class HTTPError(Exception):
            pass

        class Client:
            def __init__(self, *args, **kwargs):
                self.args = args
                self.kwargs = kwargs

            def post(self, *args, **kwargs):
                raise HTTPError("network disabled in tests")

        httpx.Client = Client
        httpx.HTTPError = HTTPError
        sys.modules["httpx"] = httpx

    if "cerebras.cloud.sdk" not in sys.modules:
        cerebras = types.ModuleType("cerebras")
        cloud = types.ModuleType("cerebras.cloud")
        sdk = types.ModuleType("cerebras.cloud.sdk")

        class Cerebras:
            def __init__(self, *args, **kwargs):
                self.args = args
                self.kwargs = kwargs

        class RateLimitError(Exception):
            pass

        sdk.Cerebras = Cerebras
        sdk.RateLimitError = RateLimitError
        cloud.sdk = sdk
        cerebras.cloud = cloud
        sys.modules["cerebras"] = cerebras
        sys.modules["cerebras.cloud"] = cloud
        sys.modules["cerebras.cloud.sdk"] = sdk


class FakeEmbedder:
    def encode(self, texts, *, normalize_embeddings: bool = False):
        vectors = []
        for text in texts:
            lowered = str(text).casefold()
            if "cat" in lowered or "猫" in lowered:
                vector = [1.0, 0.0]
            elif "dog" in lowered or "犬" in lowered:
                vector = [0.0, 1.0]
            else:
                vector = [0.5, 0.5]
            if normalize_embeddings:
                norm = sum(value * value for value in vector) ** 0.5
                vector = [value / norm for value in vector]
            vectors.append(vector)
        return vectors


class MemorySearchTests(unittest.TestCase):
    def test_sqlite_insert_and_search(self) -> None:
        from ellie.memory.memory import MemoryManager

        with TemporaryDirectory() as tmpdir:
            manager = MemoryManager(db_file=Path(tmpdir) / "memory.sqlite3", embedder=FakeEmbedder(), import_legacy=False)
            self.assertTrue(manager.remember("cat memory", emotion="joy", importance=0.9))
            self.assertTrue(manager.remember("dog memory", emotion="calm", importance=0.3))

            results = manager.search_memories("cat", top_k=2)

        self.assertEqual(results[0]["content"], "cat memory")
        self.assertEqual(results[0]["emotion"], "joy")
        self.assertGreaterEqual(results[0]["score"], results[1]["score"])

    def test_decay_scoring_prefers_recent_memory(self) -> None:
        from ellie.memory.memory import MemoryManager

        with TemporaryDirectory() as tmpdir:
            db_file = Path(tmpdir) / "memory.sqlite3"
            manager = MemoryManager(db_file=db_file, embedder=FakeEmbedder(), import_legacy=False)
            self.assertTrue(manager.remember("cat recent", emotion="", importance=0.5))
            self.assertTrue(manager.remember("cat old", emotion="", importance=0.5))

            conn = sqlite3.connect(db_file)
            try:
                conn.execute("UPDATE memories SET created_at = ? WHERE content = ?", ("2020-01-01T00:00:00+09:00", "cat recent"))
                conn.commit()
            finally:
                conn.close()

            results = manager.search_memories("cat", top_k=2)

        self.assertEqual(results[0]["content"], "cat old")
        self.assertGreater(results[0]["decay_score"], results[1]["decay_score"])


class DriveSystemTests(unittest.TestCase):
    def test_threshold_fires_and_appends_queue_entry(self) -> None:
        from ellie.autonomy.drive_system import DriveSystem

        with TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "drive.json"
            queue_file = Path(tmpdir) / "autonomy_queue.jsonl"
            drive_system = DriveSystem(
                state_file=state_file,
                queue_file=queue_file,
                clock=lambda: datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc),
                rng=mock.Mock(gauss=lambda mu, sigma: 0.0),
            )
            drive_system._state["drives"]["expression"]["value"] = 0.99
            drive_system._state["drives"]["expression"]["threshold"] = 0.68

            actions = drive_system.tick()

            queued = [json.loads(line) for line in queue_file.read_text(encoding="utf-8").splitlines() if line.strip()]

        self.assertTrue(actions)
        self.assertEqual(queued[0]["type"], "enqueue")
        self.assertEqual(queued[0]["drive_key"], "expression")
        self.assertIn("instruction", queued[0])


class RuntimeSchedulingTests(unittest.TestCase):
    def test_randomized_tick_interval_helper_uses_expected_range(self) -> None:
        from ellie.autonomy.runtime import AutonomyRuntime

        runtime = AutonomyRuntime(lambda: object())
        with mock.patch("ellie.autonomy.runtime.random.randint", return_value=42) as randint_mock:
            interval = runtime._next_tick_interval_seconds()

        self.assertEqual(interval, 42)
        randint_mock.assert_called_once_with(30, 180)


class PromptInjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _install_external_stubs()

    def test_agent_uses_relevant_memory_lookup_only(self) -> None:
        from ellie.core.agent import ReActAgent

        memory = types.SimpleNamespace(
            get_relevant_memory_context=mock.Mock(return_value="## 関連記憶\n- only needle"),
            get_memory_context=mock.Mock(side_effect=AssertionError("full memory context should not be injected")),
            record_api_call=lambda: None,
            update_task_generation_count=lambda count: None,
            add_insight=lambda text: None,
        )
        self_model = types.SimpleNamespace(get_self_context=lambda: "")
        social_needs = types.SimpleNamespace(build_system_prompt=lambda base: base)
        agent = ReActAgent(memory_manager=memory, self_model=self_model, social_needs=social_needs)

        context = agent._compose_ai_context("needle query")

        self.assertIn("only needle", context)
        memory.get_relevant_memory_context.assert_called_once_with("needle query")
