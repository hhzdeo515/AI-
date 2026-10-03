"""Request-local API measurements; no credentials or prompt bodies are logged."""
from contextlib import contextmanager
from contextvars import ContextVar

_calls = ContextVar("model_calls", default=None)


@contextmanager
def capture():
    rows = []
    token = _calls.set(rows)
    try:
        yield rows
    finally:
        _calls.reset(token)


def extend(records):
    """Merge measured calls from isolated workers into the current request."""
    rows = _calls.get()
    if rows is not None:
        rows.extend(records)


def record(model, elapsed, response=None, error=None, *, provider=None, attempts=None):
    rows = _calls.get()
    if rows is None:
        return
    usage = response.get("usage") if isinstance(response, dict) else getattr(response, "usage", None)
    if isinstance(usage, dict):
        usage = {**usage, "prompt_tokens": usage.get("input_tokens", usage.get("prompt_tokens", 0)),
                 "completion_tokens": usage.get("output_tokens", usage.get("completion_tokens", 0))}
    else:
        usage = usage.model_dump() if usage and hasattr(usage, "model_dump") else None
    rows.append({"model": model, "seconds": round(elapsed, 3), "status": "error" if error else "ok",
                 "usage": usage, "provider": provider,
                 "cost": None, "sdk_internal_attempts": attempts})
