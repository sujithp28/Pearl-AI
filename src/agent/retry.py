"""
Deterministic transient error classification for Pearl.

A transient error is one that may resolve without changing the plan —
a network timeout, a rate-limit response, a temporary lock. Retrying
the same tool call with the same arguments is a reasonable response.

A fatal error is one where retrying the same call cannot help —
a missing file, a validation failure, a permission denial. These must
follow the existing failure → replan path.

Classification is purely deterministic:
  1. Check the exception type against known-fatal and known-transient
     sets (type check wins over message check).
  2. For unknown exception types, scan the lowercased message string
     for substrings that strongly indicate transience.

This module has no side effects and no imports beyond the standard
library — it can be tested independently of the rest of Pearl.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Type classification sets
# ---------------------------------------------------------------------------

# Exceptions that are always fatal: retrying the same call cannot help.
# FileNotFoundError, PermissionError, and IsADirectoryError are all
# subclasses of OSError, so they must be listed before OSError itself
# would match — the isinstance() check uses the first match, so ordering
# matters if a subclass is present. We never list OSError here at all;
# generic OSErrors fall through to the message scan.
_FATAL_TYPES: tuple[type[BaseException], ...] = (
    ValueError,             # invalid arguments, validation failures
    TypeError,              # wrong argument type
    FileNotFoundError,      # path does not exist
    PermissionError,        # access denied
    IsADirectoryError,      # wrong path type
    NotADirectoryError,     # wrong path type
    NotImplementedError,    # unimplemented feature
    RecursionError,         # stack overflow — not a transient condition
    MemoryError,            # out of memory — retrying uses more memory
)

# Exceptions that are always transient: the same call may succeed on retry.
_TRANSIENT_TYPES: tuple[type[BaseException], ...] = (
    TimeoutError,           # covers socket.timeout via OSError.errno ETIMEDOUT
    ConnectionResetError,   # remote closed the connection unexpectedly
    ConnectionAbortedError, # connection aborted by the network
    BrokenPipeError,        # write end of a pipe closed
    BlockingIOError,        # EAGAIN / EWOULDBLOCK — resource temporarily unavailable
    InterruptedError,       # EINTR — system call interrupted; safe to retry
)

# ---------------------------------------------------------------------------
# Message pattern classification
# ---------------------------------------------------------------------------

# Lowercased substrings that indicate a transient condition when the
# exception type alone is not determinative. Checked only after the type
# sets above have been consulted.
_TRANSIENT_PATTERNS: tuple[str, ...] = (
    # HTTP error codes that indicate temporary server-side conditions
    "429",                      # Too Many Requests
    "502",                      # Bad Gateway
    "503",                      # Service Unavailable
    "504",                      # Gateway Timeout
    # Timeout language
    "timeout",
    "timed out",
    "timed_out",
    # Connection language
    "connection reset",
    "connection refused",       # may be transient for retry-able services
    "connection aborted",
    "connection error",
    # Rate limiting
    "rate limit",
    "rate_limit",
    "too many requests",
    "retry after",
    # Server availability
    "service unavailable",
    "bad gateway",
    "gateway timeout",
    "temporarily unavailable",
    "try again",
    # Low-level errno names that may appear in OSError messages
    "eagain",
    "etimedout",
    "econnreset",
    "econnrefused",
    "eintr",
)


# ---------------------------------------------------------------------------
# Public classifier
# ---------------------------------------------------------------------------


def is_transient_error(exc: BaseException) -> bool:
    """
    Return True if ``exc`` represents a condition that may resolve on
    retry without changing the tool call or the plan.

    Classification order:
      1. Known-fatal types → False (never retry).
      2. Known-transient types → True (always retry).
      3. Unknown types → scan the lowercased error message for transient
         substrings; True if any match, False otherwise.

    The function never raises.
    """

    if isinstance(exc, _FATAL_TYPES):
        return False

    if isinstance(exc, _TRANSIENT_TYPES):
        return True

    message = str(exc).lower()
    return any(pattern in message for pattern in _TRANSIENT_PATTERNS)
