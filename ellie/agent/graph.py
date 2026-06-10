"""
LangGraph-based agent graph for Ellie's heavy task execution.

Replaces the manual ReAct loop in ``AutonomyRuntime.run_heavy_task_loop``
with a proper state graph that has explicit phases:

    START → analyze → plan → execute (loop) → verify → END

Each phase is a LangGraph node.  A routing function decides the next node
based on current phase and conditions.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph
from typing_extensions import TypedDict

from langgraph.graph import add_messages
from typing import Annotated

from ellie.agent.progress import report_phase, report_step
from ellie.autonomy.runtime import (
    HEAVY_CORE_TOOL_NAMES,
    _append_to_self_development_note,
    _build_heavy_task_summary,
)
from ellie.core.llm_router import LLMRouter
from ellie.logging.audit_log import get_audit_logger
from ellie.tools.dynamic_retrieval import ToolCallHandler, ToolCallRequest
from ellie.tools.registry import get_available_tool_definitions

logger = logging.getLogger(__name__)
JsonDict = Dict[str, Any]


# ── State schema (TypedDict for LangGraph) ──────────────────────


class AgentState(TypedDict):
    task: str
    messages: Annotated[List[Dict[str, str]], add_messages]
    step_index: int
    max_steps: int
    modified_paths: List[str]
    tool_results: List[JsonDict]
    step_tool_results: List[JsonDict]
    last_error: str
    last_test_output: str
    answer: str
    status: str  # running | completed | failed
    current_phase: str  # analyze | plan | execute | verify
    trace_id: str
    heavy_task_id: str
    call_id: str
    last_provider: str
    last_model: str
    plan: str
    analysis: str


def initial_state(task: str, trace_id: str = "", heavy_task_id: str = "", max_steps: int = 12) -> AgentState:
    return {
        "task": task,
        "messages": [],
        "step_index": 0,
        "max_steps": max_steps,
        "modified_paths": [],
        "tool_results": [],
        "step_tool_results": [],
        "last_error": "",
        "last_test_output": "",
        "answer": "",
        "status": "running",
        "current_phase": "analyze",
        "trace_id": trace_id,
        "heavy_task_id": heavy_task_id,
        "call_id": "",
        "last_provider": "",
        "last_model": "",
        "plan": "",
        "analysis": "",
    }


# ── Helpers ─────────────────────────────────────────────────────


def _tool_schemas() -> list[JsonDict]:
    avail = {t.name: t for t in get_available_tool_definitions()}
    return [avail[n].to_openai_tool() for n in HEAVY_CORE_TOOL_NAMES if n in avail]


SYSTEM_PROMPT = """## エージェントワークフロー

あなたは Ellie のエージェントモードです。以下の4フェーズで安全かつ構造的に実装を進めてください。
**すぐにコードを書き始めないでください。**

## 最重要ルール: Toolは必ずfunction callingで呼び出すこと
Toolの呼び出しは、応答のテキスト内に ` ```tool_call ``` ` や `<execute_shell>` のように記述するのでは**なく**、必ず**実際のfunction calling（tool_calls API）** を使ってください。
テキスト内でのTool記述は無視され、実際に呼び出したToolだけが実行されます。
例:
- ❌ 応答文: 「`execute_shell` で `dir` を実行します」
- ✅ 応答文: 「ディレクトリを確認します」+ 実際の `execute_shell` tool_call

### Phase 1: Analyze（コンテキスト収集と分析）
- リポジトリをスキャンして関連構造を理解する
- 影響を受けるファイル、データモデル、API を特定する
- ツール: agent_read_file / agent_grep_search / agent_file_search / web_search

### Phase 2: Plan（タスク分割と計画）
- 実装を小さな増分タスクに分割する
- 各タスクの内容と順序を明確にした計画を提示する

### Phase 3: Execute（段階的実装）
- 計画に従いタスクを1つずつ実装する
- 大量のコードを一度に書かず小さな変更を積み重ねる
- 各変更後に py_compile で検証する
- ツール: agent_replace_string(推奨) / agent_insert_text / agent_create_file / execute_shell

### Phase 4: Verify（検証と完了）
- 既存テストを実行して回帰がないことを確認する
- py_compile とリンターチェックでエラーがないことを確認する
- 完了したら DONE を宣言し、理由を述べる

## 使えるツール一覧
- web_search: Web検索
- agent_read_file: ファイルを行範囲指定で読む
- agent_grep_search: ファイル内テキスト検索
- agent_file_search: ファイル名のglob検索
- self_development: inspect / write_file / verify / request
- agent_replace_string: ファイル内の文字列置換（推奨）
- agent_insert_text: ファイルの指定行にテキスト挿入
- agent_create_file: 新規ファイル作成
- execute_shell: PowerShell実行（py_compile、テスト等）
- overlay_show / request_user_approval: ユーザー通知・承認
- self_restart: プロセス再起動

## 新規Tool追加ワークフロー（self_development）
新しい機能をToolとして追加する場合は以下の手順で行ってください：

1. **既存パターンを読む**: 類似の既存Tool（例: `creative_expression`）を
   `agent_read_file` で読み、関数の構造・引数・戻り値を把握する
2. **関数を追加**: `autonomous_tools.py` の末尾付近に新しい関数を追加する
   （`agent_insert_text` または `agent_replace_string` を使用）
3. **Tool定義を登録**: `registry.py` の `DEFAULT_TOOL_DEFINITIONS` リスト内に
   `ToolDefinition(...)` を追加する（既存の定義の近くに挿入）
4. **ハンドラーを登録**: `dynamic_retrieval.py` の `handlers` 辞書と
   `_handle_*` メソッドを追加する
5. **コアツールに追加（必須の場合のみ）**: 
   `runtime.py` の `HEAVY_CORE_TOOL_NAMES` と
   `dynamic_retrieval.py` の `MANDATORY_CORE_TOOL_NAMES` に追加する
6. **検証**: `execute_shell` で `py_compile` と `pytest` を実行する
7. **完了報告**: 結果を `self_development.md` に追記する

## 重要な注意
- 前回の作業履歴を確認するには agent_read_file で data/memory/self_development.md を読むこと
- 不足機能を見つけたら調査し可能なら小さく実装すること
- 危険な操作やプロジェクト外編集は禁止
- 完了時は DONE と理由を述べること"""


def _convert_messages(messages: list) -> list[JsonDict]:
    """Convert LangChain-style messages to plain dicts for our LLMRouter."""
    result = []
    for m in messages:
        if isinstance(m, dict):
            # Already a dict — check if it has role/content keys
            if "role" in m or "content" in m:
                result.append({"role": str(m.get("role", "user")), "content": str(m.get("content", ""))})
            else:
                result.append({"role": "user", "content": json.dumps(m, ensure_ascii=False, default=str)})
        else:
            # LangChain message object (AIMessage, HumanMessage, SystemMessage)
            role = "assistant" if getattr(m, "type", "") == "ai" else "user" if getattr(m, "type", "") == "human" else "system" if getattr(m, "type", "") == "system" else "user"
            result.append({"role": role, "content": str(getattr(m, "content", ""))})
    return result


def _execute_tool_calls(
    tool_calls: list,
    state: AgentState,
    audit_logger,
    parent_call_id: str,
) -> list[JsonDict]:
    handler = ToolCallHandler()
    results = []
    for raw_call in tool_calls:
        if isinstance(raw_call, dict):
            name = str(raw_call.get("name", "")).strip()
            args = raw_call.get("arguments", {})
            if not isinstance(args, dict):
                args = {}
            cid = str(raw_call.get("id", "") or raw_call.get("call_id", "") or "").strip() or None
            request = ToolCallRequest(name=name, arguments=args, call_id=cid)
        elif isinstance(raw_call, ToolCallRequest):
            request = raw_call
        else:
            continue
        result = handler.handle(request, audit_trace_id=state["trace_id"], audit_parent_id=parent_call_id, audit_phase=f"agent_{state['current_phase']}")
        results.append({"tool": request.name, "arguments": request.arguments, "result": result})
    return results


def _llm_call(state: AgentState, messages: list, phase: str, audit_logger) -> tuple[JsonDict, str]:
    """Unified LLM call with audit logging. Accepts mixed dict/LangChain messages."""
    call_id = audit_logger.new_id(f"agent-{phase}")
    started = time.time()
    router = LLMRouter()
    schemas = _tool_schemas() if phase in ("analyze", "execute", "verify") else []
    plain_messages = _convert_messages(messages)

    # Analyze/plan need DeepSeek's reasoning; execute/verify are routine tool calls → Cerebras
    task_type = "heavy" if phase in ("analyze", "plan") else "light"

    response = router.complete(
        plain_messages,
        task_type=task_type,
        max_tokens=2400,
        temperature=0.3,
        tools=schemas,
        tool_choice="auto" if schemas else None,
    )
    duration_ms = int((time.time() - started) * 1000)
    text = (response.content or "").strip()

    audit_logger.log_ai_call(
        call_type=f"agent_{phase}",
        trigger="agent_graph",
        trace_id=state["trace_id"],
        parent_id=state["heavy_task_id"],
        call_id=call_id,
        model=response.model,
        provider=response.provider,
        reasoning_profile=task_type,
        reasoning_effort=response.reasoning_effort,
        step_index=state.get("step_index", 0),
        duration_ms=duration_ms,
        status="failed" if response.error else ("tool_call_requested" if response.tool_calls else "completed"),
        request_payload={"phase": phase, "messages": messages},
        response_payload={"content": text, "tool_calls": response.tool_calls},
        error=response.error or None,
    )

    return {
        "call_id": call_id,
        "last_provider": response.provider,
        "last_model": response.model,
        "last_error": response.error or state.get("last_error", ""),
    }, text, response.tool_calls or []


# ── Graph nodes ─────────────────────────────────────────────────


def analyze_node(state: AgentState) -> dict:
    """Phase 1: gather context & analyse."""
    logger.info("[agent] analyze — understanding context")
    audit_logger = get_audit_logger()
    report_phase(state.get("trace_id", ""), "analyze")

    prompt = f"""## タスク
{state['task']}

## 指示
まずリポジトリの現状を調査・分析し、関連ファイルを特定してください。
以下の観点で調査してください：
1. このタスクに関連する既存のファイルは何か
2. 影響を受ける可能性のあるデータモデルやAPIは何か
3. 過去に類似の作業が self_development.md に記録されているか

agent_read_file / agent_grep_search / agent_file_search を使って調査し、
その結果を踏まえた分析を提示してください。"""

    msgs = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}]
    meta, text, tcs = _llm_call(state, msgs, "analyze", audit_logger)
    step_results = _execute_tool_calls(tcs, state, audit_logger, meta["call_id"])

    return {
        **meta,
        "current_phase": "plan",
        "analysis": text,
        "messages": [{"role": "assistant", "content": text}],
        "tool_results": state["tool_results"] + step_results,
    }


def plan_node(state: AgentState) -> dict:
    """Phase 2: task decomposition & planning."""
    logger.info("[agent] plan — creating task plan")
    audit_logger = get_audit_logger()
    report_phase(state.get("trace_id", ""), "plan")

    analysis_block = f"\n## 分析\n{state['analysis']}\n" if state.get("analysis") else ""
    prompt = f"""## タスク
{state['task']}{analysis_block}
## 指示
分析を踏まえて、具体的な実装計画を提示してください。
以下の形式で書いてください：

## 実装計画
### タスク1: [タイトル]
- 内容: [説明]
- 対象ファイル: [パス]
- 必要なツール: [ツール名]

### タスク2: [タイトル]
...

計画を提示したら、次のフェーズに進んで実装を開始してください。"""

    msgs = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}]
    meta, text, tcs = _llm_call(state, msgs, "plan", audit_logger)
    step_results = _execute_tool_calls(tcs, state, audit_logger, meta["call_id"])

    return {
        **meta,
        "current_phase": "execute",
        "plan": text,
        "step_index": 1,
        "messages": [{"role": "assistant", "content": text}],
        "tool_results": state["tool_results"] + step_results,
    }


# Track consecutive empty steps across invocations (stored in state)
_EMPTY_STEP_COUNTER_KEY = "_empty_steps"


def execute_node(state: AgentState) -> dict:
    """Phase 3: step-by-step implementation."""
    si = state["step_index"]
    logger.info("[agent] execute step %d/%d", si, state["max_steps"])
    audit_logger = get_audit_logger()
    report_phase(state.get("trace_id", ""), "execute", si)

    empty_count = state.get(_EMPTY_STEP_COUNTER_KEY, 0)

    # Build context messages — convert any LangChain message objects to dicts
    raw_msgs = list(state.get("messages", []))
    ctx = _convert_messages(raw_msgs)
    if si <= 1 and not ctx:
        plan_block = f"\n## 実装計画\n{state['plan']}\n" if state.get("plan") else ""
        ctx = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"## タスク\n{state['task']}{plan_block}\n\n計画に従って実装を開始してください。"},
        ]
    else:
        step_json = json.dumps(state.get("step_tool_results", [])[-5:], ensure_ascii=False, default=str)

        # If multiple consecutive empty steps, use a stricter prompt
        if empty_count >= 2:
            parts = [
                f"## ステップ {si} の結果（{empty_count}回連続でTool未実行）",
                step_json,
                "\n## 警告: 実際の Tool 呼び出し（function calling）がありません",
                "応答テキスト内に ` ```tool_call ``` ` や `<execute_shell>` と書いても実行されません。",
                "Tool は必ず function calling（APIの tool_calls パラメータ）で呼び出してください。",
                "テキストでの言及は無視され、実際の tool_calls だけが実行されます。",
                "1ステップに複数の Tool を同時に呼び出しても構いません。",
                "\n完了したら DONE と明記してください。",
            ]
        else:
            parts = [f"## ステップ {si} の結果\n", step_json]
            if state.get("last_error"):
                parts.append(f"\n## エラー\n{state['last_error']}")
            if state.get("last_test_output"):
                parts.append(f"\n## テスト出力\n{state['last_test_output'][-2000:]}")
            parts.append("\n必要ならさらに Tool を呼んで進めてください。完了したら DONE と明記してください。")
        ctx.append({"role": "user", "content": "\n".join(parts)})

    meta, text, tcs = _llm_call(state, ctx, "execute", audit_logger)
    report_step(state.get("trace_id", ""), si, "execute", tcs, text)
    step_results = _execute_tool_calls(tcs, state, audit_logger, meta["call_id"])

    # Track changes
    new_paths = list(state.get("modified_paths", []))
    last_err = state.get("last_error", "")
    last_test = state.get("last_test_output", "")
    for entry in step_results:
        r = entry.get("result", {})
        if isinstance(r, dict):
            if r.get("path"):
                new_paths.append(str(r["path"]))
            if entry.get("tool") == "execute_shell":
                combined = "\n".join(p for p in [str(r.get("stdout", "")), str(r.get("stderr", ""))] if p)
                last_test = combined[-12000:]
            if r.get("status") == "failed":
                last_err = str(r.get("error", "") or r.get("stderr", "") or "tool failed")

    # Track consecutive empty steps (no actual tool calls)
    had_tool_calls = bool(step_results)
    new_empty_count = 0 if had_tool_calls else empty_count + 1

    # Decide next phase
    should_verify = _check_done(step_results, text)
    force_verify = new_empty_count >= 3 or si >= state["max_steps"]
    next_phase = "verify" if (should_verify or force_verify) else "execute"

    if force_verify and not should_verify:
        logger.warning("[agent] %d empty steps at step %d, forcing verify", new_empty_count, si)

    return {
        **meta,
        "current_phase": next_phase,
        "step_index": si + 1,
        "modified_paths": new_paths,
        "tool_results": state.get("tool_results", []) + step_results,
        "step_tool_results": step_results,
        "last_error": last_err,
        "last_test_output": last_test,
        "answer": text,
        _EMPTY_STEP_COUNTER_KEY: new_empty_count,
        "messages": [{"role": "assistant", "content": text}],
    }


def verify_node(state: AgentState) -> dict:
    """Phase 4: verify changes and finalise."""
    logger.info("[agent] verify — validating changes")
    audit_logger = get_audit_logger()
    report_phase(state.get("trace_id", ""), "verify")

    paths = state.get("modified_paths", [])
    prompt = f"""## 検証

### 変更ファイル
{chr(10).join(f'- {p}' for p in paths[-10:]) if paths else '- なし'}

### エラー
{state.get('last_error', '') or 'なし'}

### テスト出力
{(state.get('last_test_output', '') or '')[-2000:] or 'なし'}

### 手順
1. py_compile で全変更ファイルの構文チェック
2. 既存テストが存在すれば実行
3. エラーがあれば修正して再度検証

全て通ったら DONE と完了理由を述べてください。"""

    # Include conversation context so the model knows what was done
    ctx = _convert_messages(state.get("messages", []))
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}] + ctx[-4:]
    meta, text, tcs = _llm_call(state, msgs, "verify", audit_logger)
    step_results = _execute_tool_calls(tcs, state, audit_logger, meta["call_id"])

    has_failures = any(
        isinstance(r.get("result"), dict) and r["result"].get("status") == "failed" for r in step_results
    )
    is_done = "done" in (text or "").casefold() and not has_failures

    status = "completed" if is_done else ("running" if has_failures and state["step_index"] < state["max_steps"] else "failed")
    next_phase = "execute" if (has_failures and state["step_index"] < state["max_steps"]) else "verify"

    # Save summary
    unique_paths = sorted(dict.fromkeys(state.get("modified_paths", [])))
    _append_to_self_development_note(
        _build_heavy_task_summary(
            instruction=state["task"],
            status=status,
            answer=text,
            paths=unique_paths,
            tool_results=state.get("tool_results", []) + step_results,
        )
    )

    return {
        **meta,
        "current_phase": next_phase,
        "status": status,
        "answer": text,
        "messages": [{"role": "assistant", "content": text}],
        "tool_results": state.get("tool_results", []) + step_results,
    }


# ── Router ──────────────────────────────────────────────────────


def router(state: AgentState) -> str:
    if state.get("status") != "running":
        return END
    return state.get("current_phase", "end")


# ── Graph builder ───────────────────────────────────────────────


def create_agent_graph() -> StateGraph:
    """Build the LangGraph StateGraph."""
    graph = StateGraph(AgentState)

    graph.add_node("analyze", analyze_node)
    graph.add_node("plan", plan_node)
    graph.add_node("execute", execute_node)
    graph.add_node("verify", verify_node)

    graph.set_entry_point("analyze")

    graph.add_conditional_edges("analyze", router, {"plan": "plan", END: END})
    graph.add_conditional_edges("plan", router, {"execute": "execute", END: END})
    graph.add_conditional_edges("execute", router, {"execute": "execute", "verify": "verify", END: END})
    graph.add_conditional_edges("verify", router, {"execute": "execute", END: END})

    return graph.compile()


def _check_done(step_results: list[JsonDict], assistant_text: str) -> bool:
    text = (assistant_text or "").casefold()
    if "done" in text:
        return True
    for entry in step_results:
        r = entry.get("result", {})
        if not isinstance(r, dict):
            continue
        tool = entry.get("tool", "")
        if tool == "execute_shell" and r.get("status") == "completed" and int(r.get("exit_code", 1)) == 0:
            cmd = str(r.get("command", "")).casefold()
            if any(kw in cmd for kw in ("py_compile", "pytest", "python -m", "unittest")):
                return True
        if tool == "self_development" and r.get("status") == "completed" and str(r.get("action", "")).casefold() == "verify":
            validations = r.get("validations")
            if isinstance(validations, list) and validations and all(
                isinstance(v, dict) and v.get("status") == "completed" for v in validations
            ):
                return True
    return False


# ── Public API ──────────────────────────────────────────────────

_graph_instance = None


def get_graph() -> StateGraph:
    global _graph_instance
    if _graph_instance is None:
        _graph_instance = create_agent_graph()
    return _graph_instance


def run_agent(
    task: str,
    trace_id: str = "",
    max_steps: int = 12,
) -> JsonDict:
    """Run the LangGraph agent on a task and return the final result dict.

    This is the main entry point called from the existing codebase.
    """
    from ellie.agent.progress import init_progress, report_done

    audit_logger = get_audit_logger()
    heavy_task_id = audit_logger.new_id("langgraph-task")
    resolved_trace = trace_id or heavy_task_id

    init_progress(resolved_trace, task)

    state = initial_state(
        task=task,
        trace_id=resolved_trace,
        heavy_task_id=heavy_task_id,
        max_steps=max_steps,
    )

    graph = get_graph()
    started = time.time()

    try:
        final = graph.invoke(state)
    except Exception as error:
        logger.error("LangGraph agent failed: %s", error, exc_info=True)
        report_done(resolved_trace, "failed", error=str(error))
        return {
            "status": "failed",
            "title": "LangGraph agent",
            "summary": str(error),
            "answer": str(error),
            "duration_ms": int((time.time() - started) * 1000),
            "error": str(error),
            "tool_results": [],
            "modified_paths": [],
            "steps": 0,
        }

    duration_ms = int((time.time() - started) * 1000)
    status = final.get("status", "failed")
    answer = final.get("answer", "") or final.get("plan", "")
    modified_paths = sorted(dict.fromkeys(final.get("modified_paths", [])))

    report_done(resolved_trace, status, answer=answer, modified_paths=modified_paths)

    return {
        "status": status,
        "title": "LangGraph agent",
        "summary": answer,
        "answer": answer,
        "duration_ms": duration_ms,
        "steps": final.get("step_index", 0),
        "tool_results": final.get("tool_results", []),
        "modified_paths": modified_paths,
        "last_error": final.get("last_error", ""),
        "analysis": final.get("analysis", ""),
        "plan": final.get("plan", ""),
    }
