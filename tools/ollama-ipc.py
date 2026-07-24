#!/usr/bin/env python3
"""Deprecated Ollama filesystem-IPC client.

The old unauthenticated filesystem bridge was removed because shared local
files are not an authentication boundary. Use the authenticated YOUI Web API
from an authorized browser session, or a provider client with its own scoped
credential.
"""

import sys


MESSAGE = (
    "Ollama filesystem IPC is disabled: the unauthenticated bridge was removed. "
    "Use an authenticated YOUI session or a scoped provider client."
)


def main() -> int:
    """Fail closed for every legacy invocation."""
    print(MESSAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
