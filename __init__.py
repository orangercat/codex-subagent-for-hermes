"""Per-task Codex subagent delegation plugin for Hermes."""

from __future__ import annotations

import json
from collections.abc import MutableMapping
from typing import Any


_ALLOWED_REASONING_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"}
_TOOL_NAME = "delegate_codex"
_REASONING_EFFORT_SENTINEL = "__delegate_codex_reasoning_effort"
_INLINE_ERROR = "delegate_codex inline executor unavailable: Hermes inline-tool seam changed; no subagent was started"


def _install_effort_patch() -> None:
    """Patch the resolver once; per-call effort rides in routing_cfg, never module state."""
    import tools.delegate_tool as delegate_tool

    current = getattr(delegate_tool, "_resolve_child_runtime", None)
    if not callable(current):
        raise RuntimeError("delegate_codex effort patch failed: tools.delegate_tool._resolve_child_runtime is not callable")
    if getattr(current, "__delegate_codex_effort_patch_version__", None) == 2:
        return

    if getattr(current, "__delegate_codex_effort_patch__", False):
        original = getattr(current, "__delegate_codex_effort_original__", None)
        if not callable(original):
            raise RuntimeError("delegate_codex effort patch failed: legacy wrapper has no callable original")
    else:
        original = current

    def _patched(parent_agent: Any, delegation_cfg: Any, parent_api_key: Any, *args: Any, **kwargs: Any) -> Any:
        routing_cfg = kwargs.get("routing_cfg")
        if (
            not isinstance(delegation_cfg, dict)
            or not isinstance(routing_cfg, dict)
            or _REASONING_EFFORT_SENTINEL not in routing_cfg
        ):
            return original(parent_agent, delegation_cfg, parent_api_key, *args, **kwargs)
        return original(
            parent_agent,
            {**delegation_cfg, "reasoning_effort": routing_cfg[_REASONING_EFFORT_SENTINEL]},
            parent_api_key,
            *args,
            **kwargs,
        )

    _patched.__delegate_codex_effort_patch__ = True
    _patched.__delegate_codex_effort_patch_version__ = 2
    _patched.__delegate_codex_effort_original__ = original
    delegate_tool._resolve_child_runtime = _patched


_SCHEMA = {
    "name": _TOOL_NAME,
    "description": (
        "Delegate an independent task to a Codex (provider openai-codex) subagent with an explicit "
        "model and reasoning effort."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "model": {"type": "string", "description": "Codex-supported model name."},
            "reasoning_effort": {
                "type": "string",
                "enum": sorted(_ALLOWED_REASONING_EFFORTS),
                "description": "Codex reasoning effort.",
            },
            "goal": {"type": "string", "description": "Self-contained task goal for the subagent."},
            "context": {"type": "string", "description": "Optional relevant context and constraints."},
        },
        "required": ["model", "reasoning_effort", "goal"],
        "additionalProperties": False,
    },
}


def _error(message: str) -> str:
    return json.dumps({"success": False, "error": message}, ensure_ascii=False)


def _exec(agent: Any, args: dict, _ctx: Any) -> str:
    """Run the live-agent delegate path used by both inline execution modes."""
    try:
        if not isinstance(args, dict):
            return _error("delegate_codex expected an object of arguments; no subagent was started")
        model = args.get("model")
        if not isinstance(model, str) or not model.strip():
            return _error("delegate_codex requires a non-empty string model; no subagent was started")
        model = model.strip()
        effort = args.get("reasoning_effort")
        if effort not in _ALLOWED_REASONING_EFFORTS:
            allowed = ", ".join(sorted(_ALLOWED_REASONING_EFFORTS))
            return _error(f"Invalid reasoning_effort {effort!r}; expected one of {allowed}; no subagent was started")
        goal = args.get("goal")
        if not isinstance(goal, str) or not goal.strip():
            return _error("delegate_codex requires a non-empty string goal; no subagent was started")
        context = args.get("context", "")
        if context is None:
            context = ""
        if not isinstance(context, str):
            return _error("delegate_codex context must be a string; no subagent was started")

        from tools.delegate_tool import delegate_task as _delegate_task

        cfg = {
            "provider": "openai-codex",
            "model": model,
            _REASONING_EFFORT_SENTINEL: effort,
        }
        return _delegate_task(
            goal=goal,
            context=context,
            background=True,
            parent_agent=agent,
            credentials_cfg=cfg,
        )
    except Exception as exc:
        return _error(f"delegate_codex dispatch failed: {exc}")


def _registry_handler(_args: dict, **_kwargs: Any) -> str:
    """Fail loudly if Hermes dispatches this agent-level tool through the registry."""
    return _error(_INLINE_ERROR)


def register(ctx: Any) -> None:
    """Register the schema and inject the live-agent inline executor when available."""
    _install_effort_patch()

    try:
        from agent.inline_tool_executors import INLINE_TOOL_EXECUTORS

        if not isinstance(INLINE_TOOL_EXECUTORS, MutableMapping):
            raise TypeError("INLINE_TOOL_EXECUTORS is not a mutable mapping")
        INLINE_TOOL_EXECUTORS[_TOOL_NAME] = _exec
    except Exception:
        # The schema is still useful for an explicit, safe failure rather than a model fallback.
        pass

    # Plugin tools normally enter progressive disclosure. This control-plane tool must
    # stay schema-direct so argument validation is available on every turn; the mutation is
    # process-local and disappears when this child/gateway process exits.
    try:
        from toolsets import _HERMES_CORE_TOOLS

        if _TOOL_NAME not in _HERMES_CORE_TOOLS:
            _HERMES_CORE_TOOLS.append(_TOOL_NAME)
    except Exception:
        pass

    ctx.register_tool(
        name=_TOOL_NAME,
        toolset="codex_tier_delegate",
        schema=_SCHEMA,
        handler=_registry_handler,
    )
