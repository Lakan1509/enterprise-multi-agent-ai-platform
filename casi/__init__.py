"""CASI — AI-native agent orchestration platform (Milestone 1).

The deliverable package. All components are importable independently;
``AIKernel`` is the composition root. Optional component modules built by
other builders are imported lazily so ``casi`` stays importable in isolation.
"""

__version__ = "0.1.0"

__all__ = ["AIKernel", "Settings", "__version__"]


def __getattr__(name: str):
    """Lazily resolve top-level exports (PEP 562).

    ``casi.kernel`` is built by another builder; eager imports here would make
    the whole ``casi`` package unimportable until every component lands, so
    the heavy names resolve on first attribute access instead.
    """
    if name == "AIKernel":
        from casi.kernel import AIKernel

        return AIKernel
    if name == "Settings":
        from casi.config import Settings

        return Settings
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
