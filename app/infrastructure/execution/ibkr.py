"""IBKR adapter boundary.

Live routing is intentionally unavailable in Phase 5. The adapter can only be
introduced after explicit live authorization and production certification.
"""


class IBKRExecutionAdapter:
    def __init__(self, *args, **kwargs):
        raise RuntimeError("IBKR live execution is disabled by policy")

