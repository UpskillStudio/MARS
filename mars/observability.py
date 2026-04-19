"""
Langfuse v4 observability via the @observe decorator pattern.
Gracefully disabled if keys are not set.

Usage in coordinator:
    from mars.observability import observe_span

    @observe_span("decompose")
    async def _decompose(self, topic): ...
"""
import os
from functools import wraps
from typing import Any, Callable

_enabled = False


def _init():
    global _enabled
    pub = os.getenv("LANGFUSE_PUBLIC_KEY", "")
    sec = os.getenv("LANGFUSE_SECRET_KEY", "")
    if not pub or not sec:
        return
    try:
        # Configure via env vars — Langfuse v4 reads these automatically
        os.environ.setdefault("LANGFUSE_PUBLIC_KEY", pub)
        os.environ.setdefault("LANGFUSE_SECRET_KEY", sec)
        os.environ.setdefault(
            "LANGFUSE_HOST",
            os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com"),
        )
        from langfuse.decorators import langfuse_context  # noqa: F401 — verify importable
        _enabled = True
        print("[Observability] Langfuse v4 enabled")
    except Exception as e:
        print(f"[Observability] Langfuse init failed (tracing disabled): {e}")


_init()


def observe_span(name: str):
    """
    Decorator that wraps an async method as a Langfuse observed span.
    No-op if Langfuse is not configured.
    """
    def decorator(fn: Callable):
        if not _enabled:
            return fn
        try:
            from langfuse.decorators import observe

            @observe(name=name)
            @wraps(fn)
            async def wrapper(*args, **kwargs):
                return await fn(*args, **kwargs)

            return wrapper
        except Exception:
            return fn

    return decorator


def observe_trace(name: str):
    """
    Decorator that marks an async method as the root Langfuse trace.
    No-op if Langfuse is not configured.
    """
    def decorator(fn: Callable):
        if not _enabled:
            return fn
        try:
            from langfuse.decorators import observe

            @observe(name=name, as_type="trace")
            @wraps(fn)
            async def wrapper(*args, **kwargs):
                return await fn(*args, **kwargs)

            return wrapper
        except Exception:
            return fn

    return decorator


def flush():
    """Flush pending events to Langfuse. Call at end of run."""
    if not _enabled:
        return
    try:
        from langfuse.decorators import langfuse_context
        langfuse_context.flush()
    except Exception:
        pass
