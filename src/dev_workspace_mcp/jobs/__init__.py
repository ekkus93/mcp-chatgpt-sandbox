"""Job store and lifecycle (§15, §23, §67) — S14.

`store.py` persists job records; `reconcile.py` runs once at daemon
startup to resolve jobs left non-terminal by an unclean shutdown. Neither
module starts, streams, or kills a real process — that belongs to the
execution service (S15, S16), which uses this store rather than owning
its own bookkeeping.
"""

from __future__ import annotations
