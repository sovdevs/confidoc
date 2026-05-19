"""CLI for managing Confidoc users.

Usage:
  uv run confidoc-auth create-user <username>
  uv run confidoc-auth list-users
  uv run confidoc-auth delete-user <username>
"""

from __future__ import annotations

import getpass
import sys


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print("Usage: confidoc-auth <command> [args]")
        print("Commands: create-user <username> | list-users | delete-user <username>")
        sys.exit(1)

    cmd = args[0]

    if cmd == "create-user":
        if len(args) < 2:
            print("Usage: confidoc-auth create-user <username> [--password <pw>]")
            sys.exit(1)
        username = args[1]
        # Support --password flag for non-interactive use (e.g. Render one-off shell)
        if "--password" in args:
            idx = args.index("--password")
            if idx + 1 >= len(args):
                print("--password requires a value")
                sys.exit(1)
            password = args[idx + 1]
            confirm  = password
        else:
            password = getpass.getpass(f"Password for '{username}': ")
            confirm  = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("Passwords do not match.")
            sys.exit(1)
        try:
            from app.auth.users import create_user
            create_user(username, password)
            print(f"User '{username}' created/updated.")
        except ValueError as e:
            print(f"Error: {e}")
            sys.exit(1)

    elif cmd == "list-users":
        from app.auth.users import list_users
        users = list_users()
        if users:
            print("\n".join(users))
        else:
            print("No users configured.")

    elif cmd == "delete-user":
        if len(args) < 2:
            print("Usage: confidoc-auth delete-user <username>")
            sys.exit(1)
        username = args[1]
        import json
        from app.config import settings
        if not settings.users_file.exists():
            print("No users file found.")
            sys.exit(1)
        users = json.loads(settings.users_file.read_text(encoding="utf-8"))
        before = len(users)
        users = [u for u in users if u["username"] != username]
        if len(users) == before:
            print(f"User '{username}' not found.")
            sys.exit(1)
        settings.users_file.write_text(json.dumps(users, indent=2), encoding="utf-8")
        print(f"User '{username}' deleted.")

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
