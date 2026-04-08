# Heart Daemon — The sovereign heartbeat of Love.
#
# Architecture:
#   gather.py   — Collects all system state before brain invocation
#   invoke.py   — Calls Claude Code with state, parses JSON decisions
#   execute.py  — Executes Claude's decisions (spawns, HIVE, files, etc.)
#   daemon.py   — Main loop: gather → invoke → execute, every 7 minutes
