"""Errors a store may raise for a tool to show. Domains subclass these for their own rules."""


class StoreError(Exception):
    """An anticipated failure whose message is safe and useful to show an agent."""


class NotFoundError(StoreError):
    pass


class ConflictError(StoreError):
    pass


class BusyError(StoreError):
    """The data store could not serve the call in time (lock held, contention, or offline)."""


class CallTimeoutError(StoreError):
    """A call ran past the per-call deadline and was cancelled. Nothing was written."""
