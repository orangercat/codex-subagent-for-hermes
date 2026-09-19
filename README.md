# Hermes Codex Delegate

A Hermes plugin that starts an `openai-codex` subagent for an independent task. Each call chooses the Codex model and reasoning effort explicitly.

## Requirements

- Hermes Agent `>=0.21.3, <0.22`
- An authenticated `openai-codex` provider in Hermes

## Install

```bash
hermes plugins install orangercat/codex-tier-delegate --enable
```

Restart a running Hermes gateway after installation.

## Tool

The plugin registers `delegate_codex` with these arguments:

```json
{
  "model": "gpt-5.6-terra",
  "reasoning_effort": "high",
  "goal": "Implement and verify the requested change.",
  "context": "Optional task context and constraints."
}
```

- `model`: any model available through the user's Codex account.
- `reasoning_effort`: `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`, or `ultra`.
- `goal`: a self-contained task for the subagent.
- `context`: optional supporting context.

The task runs in the background through Hermes' existing subagent machinery. Model availability is checked by Codex at dispatch time.

## Compatibility status

This is an experimental plugin. Hermes does not currently expose per-call subagent model and reasoning selection through its public plugin API, so this version patches private Hermes runtime seams. The `requires_hermes` range is intentionally narrow; re-run the checks below before widening it.

## Development

```bash
python -m py_compile __init__.py tests/verify_dual_module_regression.py
python tests/verify_dual_module_regression.py --plugin-source __init__.py
python tests/verify_dual_module_regression.py --plugin-source __init__.py --seed-legacy-v1-wrapper
hermes plugins validate . --json
hermes plugins doctor . --ci
```

## License

MIT
