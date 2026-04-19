"""
Langfuse observability for MARS.

Uses two complementary mechanisms:
  1. AnthropicInstrumentor — auto-captures model name, token usage, and cost
     on every client.messages.create call via OpenTelemetry.
  2. @observe decorator — adds named span hierarchy for coordinator phases
     (decompose, research, refinement, synthesis, report_gen).

Both are gracefully disabled if LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY
are not set.

Usage:
    from mars.observability import observe_span, observe_trace, flush
"""
import os
from functools import wraps
from typing import Callable

_enabled = False


def _init():
    global _enabled
    pub = os.getenv("LANGFUSE_PUBLIC_KEY", "")
    sec = os.getenv("LANGFUSE_SECRET_KEY", "")
    if not pub or not sec:
        return
    try:
        from langfuse import get_client, observe  # noqa: F401
        from opentelemetry.instrumentation.anthropic import AnthropicInstrumentor
        AnthropicInstrumentor().instrument()
        _enabled = True
        print("[Observability] Langfuse enabled (Anthropic auto-instrumentation active)")
    except Exception as e:
        print(f"[Observability] Langfuse init failed (tracing disabled): {e}")


_init()


def observe_trace(name: str):
    """Decorator that marks an async method as the root Langfuse trace."""
    def decorator(fn: Callable):
        if not _enabled:
            return fn
        try:
            from langfuse import observe, get_client

            _traced = observe(name=name)(fn)

            @wraps(fn)
            async def wrapper(self, topic: str, **kwargs):
                try:
                    get_client().set_current_trace_io(input={"topic": topic})
                    result = await _traced(self, topic, **kwargs)
                    get_client().set_current_trace_io(
                        output={"chars": len(result) if isinstance(result, str) else 0}
                    )
                    return result
                except Exception as e:
                    print(f"[Observability] trace error (bypassing tracing): {e}")
                    return await fn(self, topic, **kwargs)

            return wrapper
        except Exception:
            return fn

    return decorator


def observe_span(name: str):
    """Decorator that wraps an async method as a named Langfuse span."""
    def decorator(fn: Callable):
        if not _enabled:
            return fn
        try:
            from langfuse import observe

            _traced = observe(name=name)(fn)

            @wraps(fn)
            async def wrapper(*args, **kwargs):
                try:
                    return await _traced(*args, **kwargs)
                except Exception as e:
                    print(f"[Observability] span error (bypassing tracing): {e}")
                    return await fn(*args, **kwargs)

            return wrapper
        except Exception:
            return fn

    return decorator


def flush():
    """Flush pending Langfuse events. Call at end of each run."""
    if not _enabled:
        return
    try:
        from langfuse import get_client
        get_client().flush()
    except Exception:
        pass
