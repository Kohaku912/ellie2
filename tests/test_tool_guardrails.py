from __future__ import annotations

import sys
import types
import unittest
from unittest import mock
from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import datetime, timezone


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

    if "apscheduler.schedulers.background" not in sys.modules:
        apscheduler = types.ModuleType("apscheduler")
        schedulers = types.ModuleType("apscheduler.schedulers")
        background = types.ModuleType("apscheduler.schedulers.background")
        triggers = types.ModuleType("apscheduler.triggers")
        cron = types.ModuleType("apscheduler.triggers.cron")

        class BackgroundScheduler:
            def __init__(self, *args, **kwargs):
                self.running = False

            def add_job(self, *args, **kwargs):
                return None

            def start(self):
                self.running = True

            def shutdown(self, wait: bool = True):
                self.running = False

        class CronTrigger:
            def __init__(self, *args, **kwargs):
                self.args = args
                self.kwargs = kwargs

        background.BackgroundScheduler = BackgroundScheduler
        cron.CronTrigger = CronTrigger
        schedulers.background = background
        triggers.cron = cron
        apscheduler.schedulers = schedulers
        apscheduler.triggers = triggers
        sys.modules["apscheduler"] = apscheduler
        sys.modules["apscheduler.schedulers"] = schedulers
        sys.modules["apscheduler.schedulers.background"] = background
        sys.modules["apscheduler.triggers"] = triggers
        sys.modules["apscheduler.triggers.cron"] = cron


class AuditLogTests(unittest.TestCase):
    def test_read_file_base64_payload_is_metadata_only(self) -> None:
        from agent.audit_log import AuditLogger

        with TemporaryDirectory() as tmpdir:
            logger = AuditLogger(Path(tmpdir))
            logger.log_tool_call(
                tool_name="read_file_base64",
                trace_id="trace-1",
                response_payload={
                    "status": "completed",
                    "tool": "read_file_base64",
                    "path": "C:/tmp/example.txt",
                    "size": 3,
                    "sha256": "abc123",
                    "data_base64": "QUJDQUJDQUJD",
                },
            )

            log_path = next(Path(tmpdir).glob("ai_audit_*.md"))
            text = log_path.read_text(encoding="utf-8")

        self.assertNotIn("QUJDQUJDQUJD", text)
        self.assertIn('"data_base64": "[omitted]"', text)
        self.assertIn('"sha256": "abc123"', text)
        self.assertIn("binary payload omitted from audit log", text)


class ToolPromptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _install_external_stubs()

    def test_system_prompt_includes_top_level_index(self) -> None:
        from agent.dynamic_tool_rag import DynamicToolRAGController

        controller = DynamicToolRAGController()
        prompt = controller._build_system_prompt()

        self.assertTrue(prompt.startswith("## コア機能索引"))
        self.assertIn("web_search: 外部情報を調べる", prompt)
        self.assertIn("request_user_approval", prompt)
        self.assertIn("overlay_show / overlay_update / overlay_hide", prompt)

    def test_dynamic_prompt_lists_only_context_specific_tools(self) -> None:
        from agent.dynamic_tool_rag import DynamicToolRAGController
        from agent.tool_registry import get_available_tool_definitions

        controller = DynamicToolRAGController()
        tools = get_available_tool_definitions()
        selected_tools = [tool for tool in tools if tool.name in {"web_search", "twitter_post"}]
        messages = controller._build_messages("Need a tweet draft.", selected_tools)
        prompt = messages[1]["content"]

        self.assertIn("今回の状況に関連して追加されたツール: twitter_post", prompt)
        self.assertNotIn("今回の状況に関連して追加されたツール: web_search", prompt)


class SchedulerSkipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _install_external_stubs()

    def test_minute_cycle_skips_when_ai_is_active(self) -> None:
        from agent.ai_activity import get_ai_activity_tracker
        from scheduler.scheduler import AutonomousAgentScheduler

        scheduler = AutonomousAgentScheduler()
        tracker = get_ai_activity_tracker()
        called = {"value": False}

        def _should_not_run() -> None:
            called["value"] = True
            raise AssertionError("minute cycle should have been skipped")

        scheduler.agent.run_autonomous_cycle = _should_not_run  # type: ignore[method-assign]

        with tracker.active("test"):
            scheduler.autonomous_task_loop()

        self.assertFalse(called["value"])


class HeavyTaskConfigTests(unittest.TestCase):
    def test_heavy_task_step_limit_is_relaxed(self) -> None:
        import config

        self.assertGreaterEqual(config.HEAVY_TASK_MAX_STEPS, 20)

    def test_heavy_task_core_tools_include_browser_and_approval_helpers(self) -> None:
        from agent.autonomy_runtime import HEAVY_CORE_TOOL_NAMES

        self.assertIn("overlay_show", HEAVY_CORE_TOOL_NAMES)
        self.assertIn("request_user_approval", HEAVY_CORE_TOOL_NAMES)
        self.assertIn("twitter_followers_check", HEAVY_CORE_TOOL_NAMES)


class InstructionParsingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _install_external_stubs()

    def test_followers_request_is_forced_to_follower_tool(self) -> None:
        from agent.instruction_runner import _forced_twitter_followers_check_call

        call = _forced_twitter_followers_check_call("自分のツイッターのフォロワー数を確認して")

        self.assertIsNotNone(call)
        self.assertEqual(call["tool"], "twitter_followers_check")


class PlaywrightMcpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _install_external_stubs()

    def test_stale_browser_marker_triggers_reinstall(self) -> None:
        import agent.playwright_mcp as playwright_mcp

        with TemporaryDirectory() as tmpdir:
            play_dir = Path(tmpdir)
            marker = play_dir / ".browser_installed"
            marker.write_text("2026-06-09T22:11:32.775141+09:00", encoding="utf-8")

            with mock.patch.object(playwright_mcp, "PLAYWRIGHT_DIR", play_dir), mock.patch.object(
                playwright_mcp.subprocess,
                "run",
                return_value=types.SimpleNamespace(returncode=0, stdout="", stderr=""),
            ) as run_mock:
                manager = playwright_mcp.PlaywrightMcpManager()
                result = manager._ensure_browser_installed("npx")

            self.assertTrue(result["ok"])
            self.assertFalse(result.get("cached", False))
            run_mock.assert_called_once()
            self.assertIn("install-browser", run_mock.call_args.args[0])
            self.assertTrue(marker.exists())
            self.assertEqual(marker.read_text(encoding="utf-8").splitlines()[0], "browser=chrome-for-testing")


class PcToolBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _install_external_stubs()

    def test_bridge_status_autostarts_when_not_initialized(self) -> None:
        import agent.pc_tool_bridge as pc_tool_bridge

        original_bridge = pc_tool_bridge._BRIDGE
        self.addCleanup(lambda: setattr(pc_tool_bridge, "_BRIDGE", original_bridge))
        pc_tool_bridge._BRIDGE = None

        fake_bridge = types.SimpleNamespace(
            get_status=lambda: {
                "host": "127.0.0.1",
                "port": 8765,
                "started": True,
                "thread_alive": True,
                "loop_running": True,
                "server_running": True,
                "client_count": 0,
                "pending_call_count": 0,
                "startup_error": None,
                "connected_tool_count": 0,
                "connected_tools": [],
            }
        )

        with mock.patch.object(pc_tool_bridge, "start_pc_tool_bridge_server", side_effect=lambda host, port: setattr(pc_tool_bridge, "_BRIDGE", fake_bridge)):
            status = pc_tool_bridge.get_pc_tool_bridge_status()

        self.assertTrue(status["started"])
        self.assertEqual(status["client_count"], 0)


class ApprovalOverlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _install_external_stubs()

    def test_immediate_request_user_approval_sends_overlay(self) -> None:
        from agent import autonomous_tools

        delivery = types.SimpleNamespace(ok=True, tool_result={"ok": True}, error=None)
        with mock.patch.object(autonomous_tools, "send_pc_tool_call", return_value=delivery) as send_mock:
            result = autonomous_tools.request_user_approval({"title": "確認して", "immediate": True})

        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["delivered"])
        send_mock.assert_called_once()
        self.assertEqual(send_mock.call_args.kwargs["timeout_seconds"], 12)
        self.assertEqual(send_mock.call_args.args[0]["tool"], "overlay_show")


class TwitterFollowersTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _install_external_stubs()

    def test_followers_check_asks_for_login_when_login_required(self) -> None:
        from agent import autonomous_tools

        snapshots = iter([
            {"result": {"content": [{"type": "text", "text": "Sign in"}]}, "status": "completed"},
        ])

        def _call(tool_name, arguments):
            if tool_name == "playwright__browser_navigate":
                return {"status": "completed", "tool": tool_name, "result": {"content": [{"type": "text", "text": "Sign in"}]}}
            if tool_name == "playwright__browser_snapshot":
                return next(snapshots)
            if tool_name == "playwright__browser_run_code_unsafe":
                return {"status": "completed", "tool": tool_name, "result": {"status": "login_required"}}
            return {"status": "completed", "tool": tool_name}

        with mock.patch("agent.playwright_mcp.call_playwright_tool", side_effect=_call), mock.patch(
            "agent.playwright_mcp.get_playwright_status",
            return_value={"ok": True},
        ), mock.patch.object(autonomous_tools, "request_user_approval", return_value={"status": "completed", "delivered": True}) as approval_mock:
            result = autonomous_tools.twitter_followers_check({})

        self.assertEqual(result["status"], "login_required")
        self.assertIn("overlay_result", result)
        approval_mock.assert_called()


class SocialNeedsRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _install_external_stubs()

    def _make_manager(self):
        from agent.social_needs import SocialNeedsManager

        tmpdir = TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        manager = SocialNeedsManager(
            state_file=Path(tmpdir.name) / "state.json",
            recovery_history_file=Path(tmpdir.name) / "history.json",
            clock=lambda: datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc),
        )
        manager.llm_router.complete = lambda *args, **kwargs: types.SimpleNamespace(error="stubbed")
        return manager

    def test_exploration_multiplier_varies_by_info_kind(self) -> None:
        manager = self._make_manager()

        manager.apply_activity_event(
            "new_external_data",
            text="web search result",
            tool_names=["web_search"],
            metadata={"tool_names": ["web_search"], "status": "completed"},
        )
        web_record = manager.recovery_history["2026-06-10"]["records"][-1]

        manager = self._make_manager()
        manager.apply_activity_event(
            "new_external_data",
            text="file read result",
            tool_names=["read_file_base64"],
            metadata={"tool_names": ["read_file_base64"], "size": 128, "status": "completed"},
        )
        file_record = manager.recovery_history["2026-06-10"]["records"][-1]

        manager = self._make_manager()
        manager.apply_activity_event(
            "new_external_data",
            text="shell result",
            tool_names=["execute_shell"],
            metadata={"tool_names": ["execute_shell"], "status": "completed", "exit_code": 0, "stdout": "ok"},
        )
        shell_record = manager.recovery_history["2026-06-10"]["records"][-1]

        self.assertEqual(web_record["info_kind"], "web_search")
        self.assertEqual(file_record["info_kind"], "file_read")
        self.assertEqual(shell_record["info_kind"], "execution_result")
        self.assertGreater(web_record["info_multiplier"], shell_record["info_multiplier"])
        self.assertGreater(shell_record["info_multiplier"], file_record["info_multiplier"])
        self.assertAlmostEqual(
            web_record["status_after"] - web_record["status_before"],
            web_record["applied_amount"],
            places=6,
        )

    def test_prompt_suffix_and_snapshot_include_recovery_profiles(self) -> None:
        manager = self._make_manager()
        manager.apply_activity_event(
            "new_external_data",
            text="searched docs",
            tool_names=["web_search"],
            metadata={"tool_names": ["web_search"], "status": "completed"},
        )

        suffix = manager.build_social_prompt_suffix()
        snapshot = manager.get_debug_snapshot()

        self.assertIn("web_search", suffix)
        self.assertIn("web_search", snapshot["_recovery_profiles"]["exploration"])

    def test_approval_and_challenge_use_distinct_subtypes(self) -> None:
        manager = self._make_manager()

        manager.apply_user_message("ありがとう、助かった")
        approval_user_record = next(
            record
            for record in manager.recovery_history["2026-06-10"]["records"]
            if record["need"] == "approval"
        )

        manager = self._make_manager()
        manager.apply_activity_event(
            "social_feedback",
            text="twitter reaction",
            tool_names=["twitter_post"],
            metadata={"tool_names": ["twitter_post"], "message": "nice", "status": "completed"},
        )
        approval_social_record = next(
            record
            for record in manager.recovery_history["2026-06-10"]["records"]
            if record["need"] == "approval"
        )

        manager = self._make_manager()
        manager.apply_activity_event(
            "medium_challenge_success",
            text="validation passed",
            tool_names=["execute_shell"],
            metadata={"tool_names": ["execute_shell"], "status": "completed", "exit_code": 0},
        )
        challenge_record = next(
            record
            for record in manager.recovery_history["2026-06-10"]["records"]
            if record["need"] == "challenge"
        )

        self.assertEqual(approval_user_record["info_kind"], "user_feedback")
        self.assertEqual(approval_social_record["info_kind"], "social_feedback")
        self.assertEqual(challenge_record["info_kind"], "medium_challenge_success")
        self.assertNotEqual(approval_user_record["info_multiplier"], approval_social_record["info_multiplier"])

    def test_approval_drive_context_includes_blog_and_twitter_actions(self) -> None:
        manager = self._make_manager()
        manager.needs["approval"].status = 0.0

        context = manager.build_drive_context()

        self.assertIn("twitter_post", context)
        self.assertIn("blog_post", context)

    def test_blog_post_recovery_uses_blog_post_kind(self) -> None:
        manager = self._make_manager()

        manager.apply_activity_event(
            "social_feedback",
            text="draft blog entry",
            tool_names=["blog_post"],
            metadata={"tool_names": ["blog_post"], "title": "hello", "body": "world", "status": "completed"},
        )

        approval_record = next(
            record
            for record in manager.recovery_history["2026-06-10"]["records"]
            if record["need"] == "approval"
        )

        self.assertEqual(approval_record["info_kind"], "blog_post")


if __name__ == "__main__":
    unittest.main()
