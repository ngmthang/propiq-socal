"""
    PropIQ - Rate Limiting (slowapi, keyed by client IP)

    @author Minh Thang Nguyen
    @version July 27, 2026
"""

from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

# Global default applies to every route unless overridden with its own
# @limiter.limit(...) decorator (auth endpoints use a much stricter limit -
# see routers/auth.py - since they're the most brute-for ceable).
limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])