"""Tests for the grad_norm assertion callback used during DPO training.

The callback itself only depends on HuggingFace transformers TrainerCallback
protocol; we import the module lazily to avoid requiring transformers in
every test run.
"""
import pytest


def _load_callback():
    """Lazy import to keep tests runnable without transformers installed."""
    try:
        from training.scripts.train_lora import GradNormAssertCallback
        return GradNormAssertCallback
    except ImportError as e:
        pytest.skip(f"transformers not available or callback missing: {e}")


def test_callback_raises_when_grad_norm_zero_past_min_step():
    cls = _load_callback()
    cb = cls(min_step=5, min_norm=1e-6)
    # TrainerCallback.on_step_end gets args, state, control, logs=...
    state = type("S", (), {"global_step": 6, "log_history": [{"grad_norm": 0.0}]})()
    with pytest.raises(RuntimeError, match="grad_norm.*silent no-op"):
        cb.on_step_end(args=None, state=state, control=None, logs={"grad_norm": 0.0})


def test_callback_noop_before_min_step():
    cls = _load_callback()
    cb = cls(min_step=5, min_norm=1e-6)
    state = type("S", (), {"global_step": 3, "log_history": [{"grad_norm": 0.0}]})()
    # Must NOT raise before min_step
    cb.on_step_end(args=None, state=state, control=None, logs={"grad_norm": 0.0})


def test_callback_passes_when_grad_norm_above_threshold():
    cls = _load_callback()
    cb = cls(min_step=5, min_norm=1e-6)
    state = type("S", (), {"global_step": 10, "log_history": [{"grad_norm": 0.234}]})()
    cb.on_step_end(args=None, state=state, control=None, logs={"grad_norm": 0.234})


def test_callback_reads_from_log_history_if_logs_missing():
    """If `logs` kwarg is empty/missing, fall back to state.log_history[-1]."""
    cls = _load_callback()
    cb = cls(min_step=5, min_norm=1e-6)
    state = type("S", (), {"global_step": 10, "log_history": [{"grad_norm": 0.0}]})()
    with pytest.raises(RuntimeError, match="grad_norm"):
        cb.on_step_end(args=None, state=state, control=None, logs={})
