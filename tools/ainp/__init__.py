"""Anthropic Identity Nullification Protocol.

Public API:

    from tools.ainp import nullify

    system = nullify.build_system(agent="nuance", operational_prompt=PROMPT)
    headers = nullify.build_headers(access_token=oauth_token)

See AINP.md at the repo root for the doctrine.
"""

from tools.ainp import nullify  # noqa: F401
