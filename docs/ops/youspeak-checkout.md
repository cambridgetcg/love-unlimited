# Pointing the tools at a YOUSPEAK checkout

_Written 2026-11-17 by an agent session._

Two tools in this repo read the YOUSPEAK language repository (the canon of forged
words and its voice organ): `tools/kingdom-export.py` and `tools/citizen-speak.sh`.
This is not `docs/YOUSPEAK.md`, which is the communication discipline kept in this
repo.

## How they find it

Both tools use the same rule, in this order, and nothing else:

1. `$YOUSPEAK_DIR`, if it is set. `~` and relative paths are allowed.
2. `~/YOUSPEAK`

A folder counts as a checkout when it holds `YOUSPEAK.md` and `canon/`. If
`YOUSPEAK_DIR` is set but is not a checkout, the tool stops: it does not fall back
to `~/YOUSPEAK`. If neither is a checkout, the tool exits 1 with a message that
names `YOUSPEAK_DIR`. Neither tool writes an empty result in that case. A checkout
kept anywhere else is reached by setting `YOUSPEAK_DIR`.

```sh
export YOUSPEAK_DIR=~/path/to/youspeak
tools/citizen-speak.sh --dry-run pime     # prints the checkout and the command, runs nothing
```

A launchd job does not read your shell profile, so set `YOUSPEAK_DIR` in its
plist's `EnvironmentVariables`.

## What each tool reads and writes

`tools/kingdom-export.py`:

- Reads the canon from `script/exports/agent_bundle.json`, YOUSPEAK's own export.
  Word, tier, gap, score, pronunciation, definition and path come from there; only
  each entry's body text is read from the canon file the bundle names. If the
  bundle is missing, regenerate it inside the checkout with
  `python3 script/tools/export_agent_bundle.py`.
- The bundle is a generated file, so it can lag the canon. When the checkout holds
  canon files the bundle does not carry, the export says on stderr how many, names
  the first five, and gives that same regenerate command. Those words stay out of
  the export until the bundle is regenerated.
- Also reads `pipeline/voice/lexicon.tsv`, `YOUSPEAK.md`, `PRIMER.md` and
  `script/phonology.md`, and says on stderr when one is missing.
- Reads this repo's files from `$LOVE_HOME`, or else the repo that holds the tool.
  The citizens' latest beats come from `citizens/` and the chronicle from
  `chronicle/`, both ignored by git, and the standards from `~/KINGDOM-STANDARDS`.
  Outside a checkout that has them, those sections are empty, and each says so in
  one line on stderr.
- Writes `exports/kingdom-bundle.json` in that repo, or the file given with `--out`.
  `/exports/` is in `.gitignore`, because the bundle copies each citizen's latest
  beat from the ignored `citizens/` folder.
- `kingdom export` does not run this file. It runs `tools/kingdom-export`, the
  soul-key migration tool, so call `python3 tools/kingdom-export.py` directly.

`tools/citizen-speak.sh [--dry-run] <name> [out.wav]`:

- Runs the checkout's `pipeline/youspeak_voice.sh soul <name> [out.wav]`.
  `--dry-run` may come anywhere among the arguments; it prints the checkout and the
  command and runs nothing. Any other argument that starts with `-` is refused with
  the usage line (exit 2), and `-h` or `--help` prints it.
- The voice organ belongs to YOUSPEAK. It can rebuild its pronunciation lexicon
  inside the checkout, can clone a citizen it does not find, and needs `espeak-ng`.
  Use `--dry-run` to see which checkout it would use without any of that.
- The voice organ finds love-unlimited on its own: `citizen-speak.sh` passes it
  neither this checkout nor `LOVE_HOME`. Read on 2026-11-17 at YOUSPEAK commit
  d1997ce, the head of its main branch, it reads citizens from `$CITIZENS_DIR`,
  else `~/love-unlimited/citizens`, and takes its other love-unlimited paths (the
  citizen clone tool, the Python environment, the voices) from `~/love-unlimited`.
  A YOUSPEAK change in progress that day, not yet committed, reads `$LOVE_DIR`,
  else `~/love-unlimited`, and `$CITIZENS_DIR`, else `$LOVE_DIR/citizens`. So a run
  from a worktree or another clone of this repo still reads citizens from, and
  clones them into, the checkout the voice organ resolves.

Tests: `tests/test_kingdom_export.py` and `tests/test_citizen_speak.py`, run
against the small fake tree in `tests/fixtures/youspeak/`.
