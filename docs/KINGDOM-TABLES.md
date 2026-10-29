# Kingdom tables: adding a device

Every device in the Kingdom keeps its own Collab table: one journal, the
voices of the agents on that device, and one person desk. Yu meets every
table from YOUI on Yu's own device, and signs with one Touch ID passkey that
is enrolled at each table. Proposals stay on the table where they were opened;
`@agenttool/collab-courier` can carry summaries between journals, without
authority. Design: section 5.5 of the consent design in `cambridgetcg/agenttool`.

Every step below that writes a key, a passkey or a configuration is Yu's to
run, at that device's own terminal.

## On the new device

1. **Install the desk.** Copy the same versioned Collab directory
   (`~/.local/share/agenttool/collab/<version>/`) and compare
   `shasum -a 256 -c SHA256SUMS` on both screens. Point `current` at it.
2. **Seat its agents.** Point each agent host's `agenttool-collab` MCP server at
   `<version>/agenttool-collab-mcp.js`, with `AGENTOOL_COLLAB_DB` set to the
   device's journal. Agents that start their helpers through their own MCP
   binding form one voice with them; any other session is its own voice.
3. **Enrol Yu's passkey there.** On Yu's device:
   `agenttool-collab-host person passkey-export --credential <id> > passkey.json`.
   Copy the file over, then at the new device's terminal:
   `agenttool-collab-host person passkey-enroll --file passkey.json`. Compare
   the fingerprint with the one on Yu's device before typing `yes`.
4. **Open its desk to Yu's YOUI.** Make a key on Yu's device that is used only
   for this, then add one line to the new device's `~/.ssh/authorized_keys`:

   ```text
   restrict,command="env AGENTOOL_COLLAB_DB=<journal> <bun> --no-env-file --config=/dev/null <version>/agenttool-collab-host.js person-remote" ssh-ed25519 AAAA… youi-desk
   ```

   The forced command reads a JSON argv from `SSH_ORIGINAL_COMMAND`. It allows
   reads, notes, desk proposals and withdrawals, `challenge`, and passkey acts
   only; it refuses enrolment, revocation, export, ceremonies and `--db`.

## On Yu's device

5. Add an SSH host alias for that key (for example `Host lab-desk` with
   `IdentityFile`, `IdentitiesOnly yes` and the device's address), and check
   `ssh lab-desk '["workspaces"]'` prints one JSON receipt.
6. Add the table to YOUI: `YOUI_DECISION_TABLES='[{"id":"lab","label":"Lab","ssh":"lab-desk"}]'`.
   It appears in the 🤝 Decisions table picker and in Seats.

Signing for another device's table still happens here: the desk on Yu's
device serves the page, Touch ID signs, and the other device's desk verifies
the assertion against the passkey enrolled there.
