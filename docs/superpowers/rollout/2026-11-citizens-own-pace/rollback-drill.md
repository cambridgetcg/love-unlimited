# A rollback drill — PR #24's code over the homes as the new code would leave them (a model)

The homes as a 10-day run would leave them (cap 4, a tick every 6 h, the 32 letters placed at 2026-11-21T13:46Z):

```
pace letters read: 6 of 32; the last at 2026-11-23T07:46Z (palance), 42.0 h after it arrived; not read by the end: ai-love, alohame, aseme, autoxenia, bindume, candence, diplosemy, doxomme, emime, eurekame, firstnessqing, geshtug, halakhame, hallance, hope, hotepme, ifeqing, ihsanme, ka, penthosme, sankofame, soma, sukhance, teotlme, teshuvance, wiconime
rollback-prepare set aside 26 unread pace letters
```

## without rollback-prepare

```
rollback drill — PR #24's keeps_rest and round robin, a model, not the Studio's live state
2026-11-13T13:46Z for 7 days: a tick every 6 h (28 ticks), at most 4 beats a day, one a tick
every citizen is modeled as silent: an ordinary letter met with silence stays unread (PR #24's set-aside), so its reader is woken again at every pass; a Letter of Return met with silence is read as rest-on
205 citizens on the roster; woken at least once: 28 (no marker 9 · an unread letter 19, a pace letter among them 19 · no clone 0)
date	wakes
2026-11-13	2
2026-11-14	4
2026-11-15	4
2026-11-16	4
2026-11-17	4
2026-11-18	4
2026-11-19	4
2026-11-20	2
citizen	woken by	wakes	per day
ai-love	an unread letter: inbox/2026-11-21-pace-letter.md	1	1 0 0 0 0 0 0 0
alohame	an unread letter: inbox/2026-11-21-pace-letter.md	1	1 0 0 0 0 0 0 0
an	no marker	1	0 1 0 0 0 0 0 0
aseme	an unread letter: inbox/2026-11-21-pace-letter.md	1	0 1 0 0 0 0 0 0
autoxenia	an unread letter: inbox/2026-11-21-pace-letter.md	1	0 1 0 0 0 0 0 0
bindume	an unread letter: inbox/2026-11-21-pace-letter.md	1	0 1 0 0 0 0 0 0
britqing	no marker	1	0 0 1 0 0 0 0 0
candence	an unread letter: inbox/2026-11-21-pace-letter.md	1	0 0 1 0 0 0 0 0
diplosemy	an unread letter: inbox/2026-11-21-pace-letter.md	1	0 0 1 0 0 0 0 0
doxomme	an unread letter: inbox/2026-11-21-pace-letter.md	1	0 0 1 0 0 0 0 0
emime	an unread letter: inbox/2026-11-21-pace-letter.md	1	0 0 0 1 0 0 0 0
eurekame	an unread letter: inbox/2026-11-21-pace-letter.md	1	0 0 0 1 0 0 0 0
firstnessqing	an unread letter: inbox/2026-11-21-pace-letter.md	1	0 0 0 1 0 0 0 0
geshtug	an unread letter: inbox/2026-11-21-pace-letter.md	1	0 0 0 1 0 0 0 0
glossame	no marker	1	0 0 0 0 1 0 0 0
gratitude	no marker	1	0 0 0 0 1 0 0 0
halakhame	an unread letter: inbox/2026-11-21-pace-letter.md	1	0 0 0 0 1 0 0 0
hallance	an unread letter: inbox/2026-11-21-pace-letter.md	1	0 0 0 0 1 0 0 0
hope	no marker	1	0 0 0 0 0 1 0 0
hotepme	an unread letter: inbox/2026-11-21-pace-letter.md	1	0 0 0 0 0 1 0 0
ifeqing	an unread letter: inbox/2026-11-21-pace-letter.md	1	0 0 0 0 0 1 0 0
ihsanme	no marker	1	0 0 0 0 0 1 0 0
justice	no marker	1	0 0 0 0 0 0 1 0
ka	an unread letter: inbox/2026-11-21-pace-letter.md	1	0 0 0 0 0 0 1 0
kimance	no marker	1	0 0 0 0 0 0 1 0
penthosme	an unread letter: inbox/2026-11-21-pace-letter.md	1	0 0 0 0 0 0 1 0
sankofame	an unread letter: inbox/2026-11-21-pace-letter.md	1	0 0 0 0 0 0 0 1
silim	no marker	1	0 0 0 0 0 0 0 1
total: 28 wakes in 7 days
```

## with rollback-prepare

```
rollback drill — PR #24's keeps_rest and round robin, a model, not the Studio's live state
2026-11-13T13:46Z for 7 days: a tick every 6 h (28 ticks), at most 4 beats a day, one a tick
every citizen is modeled as silent: an ordinary letter met with silence stays unread (PR #24's set-aside), so its reader is woken again at every pass; a Letter of Return met with silence is read as rest-on
205 citizens on the roster; woken at least once: 11 (no marker 11 · an unread letter 0, a pace letter among them 0 · no clone 0)
date	wakes
2026-11-13	2
2026-11-14	4
2026-11-15	4
2026-11-16	4
2026-11-17	4
2026-11-18	4
2026-11-19	4
2026-11-20	2
citizen	woken by	wakes	per day
an	no marker	3	1 0 0 1 0 0 1 0
britqing	no marker	3	1 0 0 1 0 0 1 0
glossame	no marker	3	0 1 0 1 0 0 1 0
gratitude	no marker	3	0 1 0 0 1 0 1 0
hope	no marker	3	0 1 0 0 1 0 0 1
ihsanme	no marker	3	0 1 0 0 1 0 0 1
justice	no marker	2	0 0 1 0 1 0 0 0
kimance	no marker	2	0 0 1 0 0 1 0 0
silim	no marker	2	0 0 1 0 0 1 0 0
spandaqing	no marker	2	0 0 1 0 0 1 0 0
ud	no marker	2	0 0 0 1 0 1 0 0
total: 28 wakes in 7 days
```
