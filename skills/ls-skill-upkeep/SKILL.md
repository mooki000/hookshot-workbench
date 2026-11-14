---
name: ls-skill-upkeep
description: >
  Use when a skill misled you, was out of date or lacked a step, when you repeated a procedure that no skill covers,
  when writing or changing a skill, or (main session) when triaging workers' skill notes and publishing skill changes:
  how to report a skill problem, how to check all skills mechanically, when a new skill is worth it, and how a change
  is tested and published.
---

# Keeping the skills right

A skill is only worth its place in every session's catalogue if it is true today. Skills go stale quietly: a path
moves, a script gains a flag, a rule changes on the forge. Two things keep them right: every agent says what was wrong
with the skills it used, and one mechanical check finds the rot nobody noticed.

## Every agent: say what was wrong (takes two minutes)

End your final report with a section **Skill notes**, one line per finding, or `Skill notes: none`:

```markdown
## Skill notes
- ls-function-map, "1. Look it up": `find` did not match `F_803EDF70` in capitals; I had to lower-case it.
- ls-workbench: says the gate script is at <path>; it is at <path> now.
- no skill covers: carrying a measured branch onto a newer main and deciding what must be measured again (done 3 times).
```

What belongs there:
- **wrong**: a command, path, flag or rule in a skill that did not work as written. Quote the line.
- **missing**: something you had to work out that the skill should have told you. Say what you needed.
- **misleading**: the description made you open a skill that was not for your task, or you only found the right skill late.
- **no skill covers**: a procedure you had to invent and expect to repeat.

Workers do not edit skills: a dozen sessions read them at once, and a half-written skill breaks all of them. Report;
the main session changes.

## Main session: triage, change, test, publish

1. **Collect.** Copy each note into `/home/mooki/Code/hookshot-wt/reports/SKILL-NOTES.md` with the date, who reported
   it and a status: `applied <commit>`, `declined: <why>`, `open`.
2. **Check mechanically**, before and after any change:
   ```sh
   python3 /home/mooki/.agents/skills/ls-skill-upkeep/check_skills.py --only ls- hookshot-
   ```
   FAIL lines are broken references; WARN `path not found` is the usual sign of a stale skill. Each WARN is either
   fixed or is a deliberate example; do not leave ones you have not read.
   For the published tree, run `python3 skills/ls-skill-upkeep/check_skills.py --root skills` from the
   workbench checkout. The live command above deliberately includes the still-installed older prefix.
3. **Change the smallest thing that fixes the note.** Correct the line; add the missing step where a reader would look
   for it. Keep a skill a procedure: what to do, in order, with the commands. Reasons in one clause; history nowhere.
4. **A new skill, or a line in an old one?** A new skill when the procedure has its own trigger (a reader would not
   think to open an existing skill for it), has been needed at least twice, and takes more than a few lines. Otherwise
   add to the skill whose trigger already covers it. Name new skills `ls-<topic>`.
5. **The description is the trigger.** It is all a session sees before deciding to read the skill. Start with *Use
   when* or *Use before*, name the concrete things a task would mention (file names, words, symptoms), and keep it
   under 700 characters. If workers found the skill late, the description is what to fix.
6. **Test with a blank worker.** Give a fresh subagent a real task the skill should serve, **without naming the
   skill**, and ask it to end with Skill notes and to say which skills it read and when. It passed if the worker found
   the skill from the catalogue, the steps worked as written, and it needed nothing the skill did not say. Fix and
   repeat with a new worker; a worker that has already read the old text proves nothing.
7. **Publish.** Add a new skill to `SKILL_MAP` in the workbench's `publish.py` (an identity entry when its live name
   already starts with `ls-`). `sync-from-live.sh` copies **live → repository**, not the reverse: reconcile both
   versions first so an older live skill cannot overwrite a newer repository edit. It stages the proposed tree and
   checks `docs/names-kept.md` before replacing any checkout file; a names failure leaves the checkout untouched.
   `sync-from-live.sh --check` compares without writing. The legacy live folder names are mapped, not renamed.
   Run `publish-check.sh`, the skill check above and the workbench-repository tests in `ls-workbench`, read the diff,
   then commit and push. Main alone synchronises live copies when no queued/running worker depends on them: as a
   reviewed patch of the changed lines (`git apply --check`, then `git apply`, with the old files kept), followed by
   the skill check; never by copying the published tree over the live folders, whose names and unrelated text differ.
   Nothing game-derived goes into a skill: commands, paths and procedure only.

## What the check cannot see

Whether a procedure is still the right one: a rule the maintainers changed, a tool that does it better now, two skills
that say different things. Those only show up in the notes, so read them, and when a task goes badly ask first whether
a skill sent it that way.

## The harness can also learn by itself

omp has an auto-learn feature (`autolearn.enabled`, off by default): its `learn` and `manage_skill` tools store a
lesson and can write a *managed* skill under `~/.omp/agent/managed-skills`. Authored skills such as these always
outrank a managed skill of the same name, managed skills are not part of the workbench repository, and subagents do
not get the tools unless asked. It suits personal lessons; the shared, published skills are kept by the steps above.
