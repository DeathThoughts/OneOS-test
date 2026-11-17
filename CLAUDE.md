# Working rules for this fork

Standing rules from the fork's owner. They apply to every session.

## Roles

Owner's rule, revised 2026-11-17: design work goes to a subagent, mechanical
work does not.

- The main session plans, investigates, reviews and orchestrates.
- A change that needs design or judgement is implemented by a subagent,
  briefed with a self-contained task: a new feature, a bug fix, anything in
  the guest's boot path (guest_start.sh, guest_prepare.sh, oneos_switch.sh,
  return_to_oneui.sh) and anything touching safety, biometrics or the root
  script's writes. The main session reviews the result before it is
  committed. The owner picks the subagents' model in the session.
- The main session makes mechanical changes itself: version bumps, merges and
  syncs between branches, reverting a known commit, comments, docs and
  CLAUDE.md. It still runs the relevant tests and says what changed.

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
