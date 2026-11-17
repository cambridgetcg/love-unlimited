#!/bin/bash
# Test stub for YOUSPEAK's voice organ: prints what it was asked to do and
# speaks nothing. tests/test_citizen_speak.py reads this line.
printf 'stub-voice root=%s args=%s\n' "$(cd "$(dirname "$0")/.." && pwd)" "$*"
