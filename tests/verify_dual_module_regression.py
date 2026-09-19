#!/usr/bin/env python3
"""Regression harness for profile-multiplexed codex-subagent-for-hermes imports.

It deliberately imports one plugin source twice under distinct package module names,
mirroring PluginLoaderMixin._directory_module_name().  The fake delegate_task is a
construction-only seam: it invokes the patched resolver with routing_cfg as a
keyword argument and never starts a child or calls an API.
"""

from __future__ import annotations

import argparse
import importlib.machinery
import importlib.util
import sys
import threading
import types
from pathlib import Path
from typing import Any

DEFAULT_SOURCE = Path.home() / ".hermes/plugins/codex-subagent-for-hermes/__init__.py"


def _install_fake_runtime(seed_legacy_v1_wrapper: bool = False) -> tuple[types.ModuleType, dict[str, Any]]:
    """Install just the private seams the plugin uses, with a recording resolver."""
    captured: dict[str, Any] = {"calls": [], "lock": threading.Lock()}

    tools_pkg = types.ModuleType("tools")
    tools_pkg.__path__ = []  # type: ignore[attr-defined]
    delegate_tool = types.ModuleType("tools.delegate_tool")

    def original_resolver(parent_agent: Any, delegation_cfg: Any, parent_api_key: Any, **kwargs: Any) -> dict[str, Any]:
        result = {
            "reasoning_effort": delegation_cfg.get("reasoning_effort") if isinstance(delegation_cfg, dict) else None,
            "routing_cfg": kwargs.get("routing_cfg"),
            "delegation_cfg": delegation_cfg,
        }
        with captured["lock"]:
            captured["calls"].append(result)
        return result

    def fake_delegate_task(*, goal: str, context: str, background: bool, parent_agent: Any, credentials_cfg: dict[str, Any]) -> dict[str, Any]:
        # This is the verified production calling convention: routing_cfg is keyword-only at this seam.
        delegation_cfg = {"reasoning_effort": "high"}
        captured["last_delegation_cfg"] = delegation_cfg
        return delegate_tool._resolve_child_runtime(
            parent_agent,
            delegation_cfg,
            None,
            routing_cfg=credentials_cfg,
        )

    captured["original"] = original_resolver
    delegate_tool._resolve_child_runtime = original_resolver
    if seed_legacy_v1_wrapper:
        def legacy_v1_wrapper(parent_agent: Any, delegation_cfg: Any, parent_api_key: Any, **kwargs: Any) -> dict[str, Any]:
            return original_resolver(parent_agent, delegation_cfg, parent_api_key, **kwargs)

        legacy_v1_wrapper.__delegate_codex_effort_patch__ = True
        legacy_v1_wrapper.__delegate_codex_effort_original__ = original_resolver
        delegate_tool._resolve_child_runtime = legacy_v1_wrapper
    delegate_tool.delegate_task = fake_delegate_task
    tools_pkg.delegate_tool = delegate_tool
    sys.modules["tools"] = tools_pkg
    sys.modules["tools.delegate_tool"] = delegate_tool

    agent_pkg = types.ModuleType("agent")
    agent_pkg.__path__ = []  # type: ignore[attr-defined]
    inline = types.ModuleType("agent.inline_tool_executors")
    inline.INLINE_TOOL_EXECUTORS = {}
    agent_pkg.inline_tool_executors = inline
    sys.modules["agent"] = agent_pkg
    sys.modules["agent.inline_tool_executors"] = inline

    toolsets = types.ModuleType("toolsets")
    toolsets._HERMES_CORE_TOOLS = []
    sys.modules["toolsets"] = toolsets

    plugins_pkg = types.ModuleType("hermes_plugins")
    plugins_pkg.__path__ = []  # type: ignore[attr-defined]
    sys.modules["hermes_plugins"] = plugins_pkg
    return delegate_tool, captured


def _load(source: Path, name: str) -> types.ModuleType:
    loader = importlib.machinery.SourceFileLoader(name, str(source))
    spec = importlib.util.spec_from_loader(name, loader)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {source}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class _Ctx:
    def register_tool(self, **_kwargs: Any) -> None:
        pass


def _invoke(executor: Any, model: str, effort: str) -> dict[str, Any]:
    result = executor(
        object(),
        {"model": model, "reasoning_effort": effort, "goal": f"regression {model} {effort}"},
        None,
    )
    if not isinstance(result, dict):
        raise AssertionError(f"construction seam returned {result!r}, not a resolver result")
    return result


def _effort(result: dict[str, Any]) -> Any:
    return result["reasoning_effort"]


def run(source: Path, seed_legacy_v1_wrapper: bool = False) -> None:
    delegate_tool, captured = _install_fake_runtime(seed_legacy_v1_wrapper)
    first = _load(source, "hermes_plugins.codex_subagent_for_hermes")
    second = _load(source, "hermes_plugins.codex_subagent_for_hermes__home_stock_digest")
    first.register(_Ctx())
    first_executor = sys.modules["agent.inline_tool_executors"].INLINE_TOOL_EXECUTORS["delegate_codex"]
    first_result = _invoke(first_executor, "gpt-5.6-luna", "xhigh")
    second.register(_Ctx())
    executor = sys.modules["agent.inline_tool_executors"].INLINE_TOOL_EXECUTORS["delegate_codex"]

    if seed_legacy_v1_wrapper:
        resolver = delegate_tool._resolve_child_runtime
        unwrapped = getattr(resolver, "__delegate_codex_effort_original__", None) is captured["original"]
        print(f"V2_UNWRAPS_LEGACY_V1={unwrapped}")
        assert getattr(resolver, "__delegate_codex_effort_patch_version__", None) == 2
        assert unwrapped, "v2 must replace v1 rather than wrap it"

    print(f"SOURCE={source}")
    print(f"FIRST_INSTANCE_EFFORT={_effort(first_result)}")
    print(f"INLINE_EXECUTOR_FROM_SECOND={executor is second._exec}")
    assert executor is second._exec, "the second profile must own the live inline executor"

    # This expectation is intentionally green-only: the v1 ContextVar design prints high here and fails.
    second_fast = _invoke(executor, "gpt-5.6-luna", "xhigh")
    print(f"SECOND_INSTANCE_XHIGH={_effort(second_fast)}")
    assert _effort(second_fast) == "xhigh", "xhigh must carry through routing_cfg"
    assert second_fast["routing_cfg"]["model"] == "gpt-5.6-luna", "model must carry through routing_cfg"

    second_deep = _invoke(executor, "gpt-5.6-sol", "medium")
    print(f"SECOND_INSTANCE_MEDIUM={_effort(second_deep)}")
    assert _effort(second_deep) == "medium", "medium must carry through routing_cfg"

    barrier = threading.Barrier(3)
    concurrent: dict[str, Any] = {}
    failures: list[BaseException] = []

    def worker(model: str, effort: str) -> None:
        try:
            barrier.wait()
            concurrent[effort] = _effort(_invoke(executor, model, effort))
        except BaseException as exc:  # pragma: no cover - surfaced by main thread
            failures.append(exc)

    threads = [
        threading.Thread(target=worker, args=("gpt-5.6-luna", "xhigh")),
        threading.Thread(target=worker, args=("gpt-5.6-sol", "medium")),
    ]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()
    if failures:
        raise failures[0]
    print(f"CONCURRENT_XHIGH={concurrent.get('xhigh')} CONCURRENT_MEDIUM={concurrent.get('medium')}")
    assert concurrent == {"xhigh": "xhigh", "medium": "medium"}, "concurrent calls must not cross efforts"

    ordinary = delegate_tool.delegate_task(
        goal="ordinary delegate_task",
        context="",
        background=True,
        parent_agent=object(),
        credentials_cfg={"provider": "openai-codex", "model": "ordinary-model"},
    )
    print(f"ORDINARY_DELEGATE_TASK={_effort(ordinary)}")
    assert _effort(ordinary) == "high", "ordinary delegate_task must retain config default"
    assert ordinary["delegation_cfg"] is captured["last_delegation_cfg"], "unmarked calls must pass delegation_cfg unchanged"
    print("PASS dual-module parameter-routing regression")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plugin-source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--seed-legacy-v1-wrapper", action="store_true")
    args = parser.parse_args()
    run(args.plugin_source.expanduser().resolve(), args.seed_legacy_v1_wrapper)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
