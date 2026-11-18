# Working rules for this fork

Standing rules from the fork's owner. They apply to every session.

## Roles

Owner's rule, revised 2026-11-17: the main session does the thinking, a
subagent does the hands-on work.

- The main session plans, investigates, designs and reviews. Research, root
  causes, the design of a fix and every decision stay with it.
- Every file change is made by a subagent, briefed with a self-contained,
  precise task that already carries the design: code, tests, scripts,
  version bumps, comments, docs. The owner picks the subagents' model; every
  subagent is started with that model named explicitly, never left to
  inherit the main session's.
- Light changes (a few lines, docs, CLAUDE.md, a pin or a wording fix) the
  main session makes itself (owner, 2026-11-18): a brief would cost more.
- The main session reviews every result before it is committed: a full
  review for anything in the guest's boot path (guest_start.sh,
  guest_prepare.sh, oneos_switch.sh, return_to_oneui.sh), safety,
  biometrics or the root script's writes; a quick review for mechanical
  changes. It runs the relevant tests or checks that the subagent did.
- Git and GitHub operations with no file edit (commit, push, opening and
  merging a PR, fetching a log) are done by the main session directly: a
  brief would cost more than the command.

## Work from other AIs

Owner's rule, 2026-11-18. When the owner sends another AI's report or audit
(not the owner's own words, and not a subagent of this session):

- The owner and the main session review it together first; nothing is
  implemented from it straight away.
- The main session then writes that AI a precise prompt (in English) with
  the agreed scope, and that AI implements it and opens the PR.
- The main session reviews the PR or the work and approves only what is
  right. It implements such work itself only when the owner asks it to.

## Biometrics in the guest (fingerprint and face)

- Enrollment happens in One UI only. The guest never enrolls a fingerprint or a
  face of its own, and nothing is added that makes it able to.
- The guest recognises the enrollments One UI already has: guest_prepare.sh
  carries One UI's enrollment metadata across on every switch, the templates
  stay in the phone's TEE, and the guest unlocks with them through the phone's
  own HAL.
- Safety comes first. Nothing of One UI's credentials (PIN, gatekeeper handle,
  synthetic-password blobs) is read or written. Host-side writes into the
  guest's data go through the no_symlink guard. A biometric path that fails
  must fall back to the guest's PIN, and no change may be able to lock the
  owner out of One UI.
- A change is not called working until it was tried on a phone; say plainly
  what was only read or only built.

## Performance

- Native code, not interpreted: a patched APK or jar should run compiled
  (dex2oat / odex for the patched file) instead of falling back to the
  interpreter or JIT because its oat was hidden.
- Use the GPU where the work is rendering or model inference.
- HyperOS and ColorOS are the guests that matter most for this.

## Repository

- Work lands in DeathThoughts/OneOS-test through pull requests (from the
  mlcraze5 fork too). Never push to vdcoolok/OneOS or its upstream.
- Two long-lived branches:
  - `debug`: every PR lands here first. Its APK (debug signing) is what the
    owner tries on the phone.
  - `release`: what users get. A change reaches it only after it was tried on
    a phone from `debug`. Promotion is a PR that merges `debug` into a branch
    made from `release`, plus the release build bump. Right after it is
    merged, `release` is merged back into `debug`: build-apk.yml checks that
    `debug` is based on `release`.
- Settings that must differ between the test channel and users (the relaxed
  battery gates, for example) belong to the build type, not to different code
  on the two branches, so promotion stays a plain merge. Until a setting is
  moved there, the promotion PR restores release's value and says so.
- The mlcraze5/OneOS-build fork builds APKs from its own `debug` and
  `release` branches; pushing there is only for test builds.
- Follow the repository's own style: comments say why and name the phone or
  report the fix came from; one logical change per commit.

## Research before planning a fix

Owner's rule (2026-11-16, after the ORMS screen-on boost was planned as a P1
task and turned out impossible on Samsung). Before a problem becomes a task,
and before any subagent is briefed:

- Trace the whole dependency chain of the thing to fix: which service, HAL,
  kernel node or vendor binary it ends in, and whether the Samsung host has it.
  A guest feature that needs an OPPO, Xiaomi or Google vendor HAL or kernel
  driver cannot be fixed from the guest's side.
- Prove it from the log first: the exact failing line, how often, and when.
  Say plainly what was only inferred (for example, the guest image was not
  available to read).
- Separate the owner's own settings (One UI or guest settings) from OneOS
  bugs; settings are reported, not "fixed" in code.
- Read the known dead ends in docs/dead-ends.md (do not plan those again) and
  add to it whenever a new one is found.
