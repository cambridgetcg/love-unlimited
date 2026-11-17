# A tiny YOUSPEAK tree, for tests only

Not the real YOUSPEAK. It has just the files `tools/kingdom-export.py` and
`tools/citizen-speak.sh` read, so `tests/test_kingdom_export.py` and
`tests/test_citizen_speak.py` can run without a real checkout. The two words in
it (`fixturame`, `fixturance`) are made up for the tests. The layout follows
the real checkout as of 2026-11-17: words under `canon/core/` and at the top of
`canon/`, and the bundle at `script/exports/agent_bundle.json`.
`pipeline/youspeak_voice.sh` is a stub that prints its arguments.
