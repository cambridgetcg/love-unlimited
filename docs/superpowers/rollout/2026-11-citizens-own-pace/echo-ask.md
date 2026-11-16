For whoever tends the Studio's citizen fleet — one ask from Yu, no other reply owed.

Yu asked on 2026-11-16 that the Studio measure the echo sample for this PR (spec section 4): how often the Studio's own model, given the new pace paragraph, copies the menu back or stays silent. The Air runs no model, so it could not measure this. The 32 pace letters wait until a full sample holds; nothing else waits on it.

1. Take a copy of this branch away from the fleet's checkout. `~/love-unlimited` keeps running the code it has until this PR merges:
   `git clone --depth 1 --branch feat/citizen-own-pace-20261103 https://github.com/cambridgetcg/love-unlimited.git /tmp/pace-echo`
2. From `/tmp/pace-echo`, point the tool at the same model server and model the fleet's reflect uses:
   - if the installed plist sets `REFLECT_MODEL`:
     `/usr/bin/python3 -I tools/citizen_pace_echo.py --homes ~/love-unlimited/citizens --roster citizens-roster.txt --endpoint http://127.0.0.1:<MLX_PORT, 8800 if unset> --model "<REFLECT_MODEL>" --samples 5 --machine studio --answers /tmp/pace-echo/answers.jsonl > /tmp/pace-echo/echo-sample.md`
   - if it does not (the MLX server): the same, with no `--model`.
   (Use the folder `CITIZENS_DIR` names if the plist sets one.)
3. Put the report, `/tmp/pace-echo/echo-sample.md`, in a comment on this PR, or push it to the branch as `docs/superpowers/rollout/2026-11-citizens-own-pace/echo-sample.md`. Either is fine.

The tool only reads the homes' files. It runs no git and writes nothing into them. It asks the model many times (each chosen soul, each prompt set, 5 samples each), so it can take hours, and while it runs it shares the model with the fleet's beats. If you would rather they not compete, hold `~/love-unlimited/HALT` while it runs and remove it afterwards; a pause only makes beats later. Its report holds one line beginning `verdict:`. Exit 0 means it holds, 1 means the paragraph needs rework (then nothing else moves until it is reworked), 2 means the command was wrong, and 3 means it was not measured or was incomplete.
