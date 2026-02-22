"""acrfetcher package."""

def run_cli(*args, **kwargs):
    # Lazy import so that importing acrfetcher doesn't pull the full runtime.
    from .main import run_cli as _run_cli
    return _run_cli(*args, **kwargs)

__all__ = ["run_cli"]
