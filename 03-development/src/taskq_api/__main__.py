"""[FR-03] ``python -m taskq_api`` CLI entry-point.

Implements AC-3.3: ``python -m taskq_api key create --scope <scope>``
generates a fresh key, persists its SHA-256 hash via
:class:`taskq_api.repository.key_repo.KeyRepository`, and prints the
plaintext exactly once.

The plaintext never lands on disk — only the SHA-256 hex digest does
(AC-3.2 / NFR-02). The CLI is the sole creation path; production code
must never accept plaintext keys outside of this entry-point.

Citations:
    - SPEC.md §3 FR-03 AC-3.3 (one-shot plaintext on create)
    - SPEC.md §3 FR-03 AC-3.2 (hash-only storage)
    - SAD.md §2.10
"""

from __future__ import annotations

import argparse
import secrets

from taskq_api.repository.key_repo import KeyRepository
from taskq_api.service.auth import hash_key


def _build_parser() -> argparse.ArgumentParser:
    """[FR-03 AC-3.3] Build the ``key create`` subcommand parser."""
    parser = argparse.ArgumentParser(
        prog="python -m taskq_api",
        description="taskq_api operator CLI.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    key_parser = subparsers.add_parser(
        "key",
        help="api-key administration",
    )
    key_sub = key_parser.add_subparsers(
        dest="key_command", required=True,
    )
    create = key_sub.add_parser(
        "create",
        help="mint a new api key (prints plaintext exactly once)",
    )
    create.add_argument(
        "--scope",
        required=True,
        choices=("read", "write", "admin"),
        help="scope granted to the new key",
    )
    return parser


def _cmd_key_create(args: argparse.Namespace) -> int:
    """[FR-03 AC-3.3] Mint one api key, persist its hash, print plaintext.

    The plaintext is generated with ``secrets.token_urlsafe`` and
    printed once; the repository only stores ``sha256(plaintext)``.
    """
    plaintext = secrets.token_urlsafe(32)
    repo = KeyRepository()
    repo.insert(
        key_hash=hash_key(plaintext),
        scope=args.scope,
        revoked_at=None,
    )
    print(plaintext)
    return 0


def main(argv: list[str] | None = None) -> int:
    """[FR-03 AC-3.3] CLI entry-point — dispatches to a subcommand.

    Returns the process exit code (0 on success, non-zero on usage
    errors / argparse failures).
    """
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "key" and args.key_command == "create":
        return _cmd_key_create(args)
    parser.error("unknown command")
    return 2


__all__ = ["main"]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
