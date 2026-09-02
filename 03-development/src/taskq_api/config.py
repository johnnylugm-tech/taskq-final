"""[NFR-07] TASKQ_* settings — independence module.

Reads environment variables via :mod:`pydantic_settings`. Exposes
``db_url_safe`` (password-stripped) so no production DSN ever leaves this
module in its raw form.

Citations:
    - SPEC.md §5.1 (env-var contract)
    - SPEC.md §4 NFR-04 (no DB password in logs / errors)
    - SAD.md §2.3
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """[NFR-07] Typed TASKQ_* settings.

    Citations: SPEC.md §5.1.
    """

    model_config = SettingsConfigDict(
        env_prefix="TASKQ_",
        env_file=None,
        extra="ignore",
        case_sensitive=False,
    )

    db_url: str = "sqlite:///./taskq.db"
    db_pool_size: int = 5
    cors_origins: str = ""
    log_level: str = "INFO"
    log_format: str = "json"
    host: str = "127.0.0.1"
    port: int = 8000
    task_timeout: float = 10.0
    drain_timeout: float = 5.0
    max_concurrent: int = 8
    rate_burst: int = 20
    rate_per_sec: float = 5.0

    @property
    def db_url_safe(self) -> str:
        """[NFR-04] Strip the password before exposing the DSN."""
        url = self.db_url
        if "@" not in url:
            return url
        scheme, _, rest = url.partition("://")
        if "@" not in rest:
            return url
        creds, _, hostpart = rest.partition("@")
        if ":" in creds:
            user, _ = creds.split(":", 1)
            creds = f"{user}:[REDACTED]"
        return f"{scheme}://{creds}@{hostpart}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached :class:`Settings` singleton."""
    return Settings()
