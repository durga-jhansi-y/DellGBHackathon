"""Write DATABASE_URL into the repo-root .env from a hidden prompt.

Usage (from anywhere):  python db/setup_env.py

The connection string is typed or pasted at a hidden prompt, so it never
appears on screen, in shell history, or in chat. .env is git-ignored.
"""
import getpass
import re
import sys
from pathlib import Path

ENV = Path(__file__).resolve().parent.parent / ".env"


def main() -> int:
    url = getpass.getpass("Paste the Tiger connection string (hidden, then Enter): ").strip()
    if not re.match(r"^postgres(ql)?://[^:/@]+:[^@]+@[^/]+/\w+", url):
        print("That does not look like postgres://user:password@host:port/db. Nothing written.")
        return 1

    # Keep any other lines already in .env; replace old DATABASE_URL / PGPASSWORD.
    kept = []
    if ENV.exists():
        for line in ENV.read_text(encoding="utf-8").splitlines():
            if not re.match(r"^\s*(DATABASE_URL|PGPASSWORD)\s*=", line):
                kept.append(line)
    kept.append(f"DATABASE_URL={url}")
    ENV.write_text("\n".join(kept) + "\n", encoding="utf-8")

    host = re.search(r"@([^:/]+)", url).group(1)
    print(f"Wrote {ENV}")
    print(f"Host: {host[:6]}****  (password saved, not shown)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
