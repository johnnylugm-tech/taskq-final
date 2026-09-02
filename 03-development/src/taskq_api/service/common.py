"""[FR-01, NFR-04] Service-layer shared helpers.

* ``now()`` — injectable UTC clock used by task timestamps, key revocation
  checks, bucket refill, and run durations.
* ``sanitize_text()`` — applies the FR-01 field rule set (non-empty,
  ≤1000 chars, injection-character blacklist) plus [NFR-04] secret
  redaction on anything that can echo a subprocess.

Citations:
    - SPEC.md §3 FR-01 AC-1.2 (validation rules)
    - SPEC.md §4 NFR-04 (secret redaction)
    - SAD.md §2.7
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Iterable

from taskq_api.errors import ValidationProblem, redact_secrets

# FR-01 AC-1.2 — injection-character blacklist.
# We ban characters that have no legitimate use in shell-style command lines
# (backticks, $(), ;, &&, |, >, <, \n) — keeps the input purely as data,
# which matches SAD.md §2.7's "no shell=True" posture.
_INJECTION_CHARS = re.compile(r"[`$();|&><\n\r]")


def now() -> datetime:
    """[FR-01] UTC clock — single injectable source of timestamps."""
    return datetime.now(timezone.utc)


def sanitize_text(text: str, *, field: str = "text") -> str:
    """[FR-01, NFR-04] Validate a free-text field and redact secrets.

    Raises ``ValidationProblem`` on rule violation so the handler turns it
    into a 422 + ``application/problem+json`` body.
    """
    if len(text) == 0:
        raise ValidationProblem(f"{field} must not be empty")
    if len(text) > 1000:
        raise ValidationProblem(f"{field} must be ≤1000 chars")
    if _INJECTION_CHARS.search(text):
        raise ValidationProblem(f"{field} contains forbidden characters")
    return redact_secrets(text)


def chunked(seq: Iterable, size: int):
    """Yield successive ``size``-element chunks of ``seq``."""
    buf: list = []
    for item in seq:
        buf.append(item)
        if len(buf) >= size:
            yield buf
            buf = []
    if buf:
        yield buf
