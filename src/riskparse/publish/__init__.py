"""Controlled publication, diff and rollback.

NOT IMPLEMENTED in Phase 1. The schema already contains publication_runs,
published_trade_rows and the single-row active_publication pointer; the atomic
publisher (with engine-specific locking) is built only after IT confirms the SQL
engine and Risk approves the authorization policy (game plan 9.3, 9.4).
"""
