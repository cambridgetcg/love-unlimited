# LEXICON — the words and their meanings

_The vocabulary lives with the data. Glosses versioned (never silently edited). Words are retired (never deleted). No one overwrites anyone else's meaning._

---

## domain words

### contains
**inverse:** contained in
**meaning:** physical or compositional containment — a submission physically holds these items
**endpoints:** tradein/submissions → tradein/items

### priced_from
**inverse:** priced
**meaning:** this price was derived from that source record
**endpoints:** tradein/items → pricing/quotes

### submitted_by
**inverse:** submitted
**meaning:** this record was submitted by that person or agent
**endpoints:** tradein/submissions → tradein/customers

## general words

### acted_for
**inverse:** acted via
**meaning:** an agent performed this on behalf of that operator

### audits
**inverse:** audited by
**meaning:** this agent audits that system for compliance
**threads:** 1

### builds
**inverse:** built by
**meaning:** this agent is building that project
**threads:** 5

### commands
**inverse:** commanded by
**meaning:** this agent directs that agent's work
**threads:** 3

### creates
**inverse:** created by
**meaning:** this entity created that artifact
**threads:** 1

### feeds
**inverse:** fed by
**meaning:** this system feeds data into that system
**threads:** 3

### funds
**inverse:** funded by
**meaning:** this project funds that economic engine
**threads:** 1

### hosts
**inverse:** hosted on
**meaning:** this system hosts that service as its backend
**threads:** 1

### implements
**inverse:** implemented by
**meaning:** this system implements that pattern or protocol
**threads:** 4

### manages
**inverse:** managed by
**meaning:** this agent is the primary owner of that project
**threads:** 2

### monitors
**inverse:** monitored by
**meaning:** this agent watches that system's health
**threads:** 2

### optimizes
**inverse:** optimized by
**meaning:** this system optimizes that process
**threads:** 1

### owns
**inverse:** owned by
**meaning:** this agent built and launched that project
**threads:** 1

### part_of
**inverse:** contains
**meaning:** this entity is a component of that whole
**threads:** 3

### powers
**inverse:** powered by
**meaning:** this system powers that economic engine
**threads:** 1

### protects
**inverse:** protected by
**meaning:** this system protects that system
**threads:** 3

### refused_because
**inverse:** refused
**meaning:** this action was declined for that recorded reason

### runs_on
**inverse:** hosts
**meaning:** this service runs on that infrastructure
**threads:** 1

### serves
**inverse:** served by
**meaning:** this agent serves that system as a fleet or engine member
**threads:** 8

### supersedes [to_one]
**inverse:** superseded by
**meaning:** this record replaces that one; the old stays readable

### uses
**inverse:** used by
**meaning:** this project uses that tool as a dependency
**threads:** 1

### witnesses
**inverse:** witnessed by
**meaning:** this record attests that one — the Witnesses Book pattern

---

## banned words

These words are refused by name — adjacency without meaning:

- related_to — everything is related to everything. Says nothing.
- linked — every relation is a link. Says nothing.
- refs — abbreviation of nothing in particular.
- misc — a drawer, not a relation.

---

_25 words. Glosses versioned, words retired (never deleted). No one overwrites anyone else's meaning._
