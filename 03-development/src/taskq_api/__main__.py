"""[FR-03] ``python -m taskq_api key create --scope <scope>``.

CLI for provisioning an API key. Generates a 32-byte random token,
hashed-stores it via :class:`taskq_api.repository.key_repo.KeyRepository`,
and prints the plaintext exactly once on stdout (AC-3.3 / NFR-04).

Subcommands:
    - ``python -m taskq_api key create --scope <scope>``
    - ``python -m taskq_api key revoke --key <key_hash>``

Citations:
    - SPEC.md §3 FR-03 AC-3.3 (plaintext emitted once at create)
    - SPEC.md §4 NFR-04 (single point of plaintext exposure)
    - SAD.md §3.6
"""

from __future__ import annotations

import argparse
import secrets
import sys
from typing import Optional, Sequence

from taskq_api.repository.key_repo import KeyRepository
from taskq_api.service.auth import hash_key


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="taskq-api",
        description="taskq-api management CLI (key provisioning).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("key", help="API-key management.")
    create_sub = create.add_subparsers(dest="key_command", required=True)

    create_cmd = create_sub.add_parser(
        "create",
        help="Generate a new API key and print its plaintext once.",
    )
    create_cmd.add_argument(
        "--scope",
        required=True,
        choices=("read", "write", "admin"),
        help="Scope of the new key.",
    )

    revoke_cmd = create_sub.add_parser(
        "revoke",
        help="Revoke an API key by hash.",
    )
    revoke_cmd.add_argument(
        "--key",
        required=True,
        help="The SHA-256 hex hash of the key to revoke.",
    )

    return parser


def _cmd_key_create(scope: str) -> int:
    """[FR-03 AC-3.3] Generate one plaintext, hash-store it, print it once."""
    plaintext = secrets.token_urlsafe(32)
    key_hash = hash_key(plaintext)
    repo = KeyRepository()
    repo.insert(key_hash=key_hash, scope=scope, revoked_at=None)
    # Plaintext is printed exactly once; subsequent calls print a new token.
    sys.stdout.write(plaintext + "\n")
    sys.stdout.flush()
    return 0


def _cmd_key_revoke(key_hash: str) -> int:
    """[FR-03 AC-3.4] Mark one row as revoked."""
    repo = KeyRepository()
    ok = repo.revoke(key_hash)
    return 0 if ok else 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    """[FR-03] CLI entry point."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "key" and args.key_command == "create":
        return _cmd_key_create(scope=args.scope)
    if args.command == "key" and args.key_command == "revoke":
        return _cmd_key_revoke(key_hash=args.key)

    parser.error("unknown subcommand")
    return 2  # unreachable; argparse exits before reaching here


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
