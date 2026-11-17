# Fork support and patch hosting

Support belongs to [DeathThoughts/OneOS-test issues](https://github.com/DeathThoughts/OneOS-test/issues).
In Last session, collect logs and tap **Report on GitHub**. Probe findings have the
same report action. Preview the redacted files, save the complete ZIP, and open
the issue. The app copies the report details with line breaks and opens a short
prefilled draft in your browser. Add the problem and reproduction steps, paste
the copied details, attach the saved ZIP, review it and submit. GitHub requires
an account. Opening the browser does not submit the issue or upload the ZIP.

The report includes the collecting app build, phone model, collected summary,
and report.json metadata such as session outcome, image/profile, collection
failures and redaction counts when present. The ZIP retains the complete
collected logs, report.json and file hashes. Disabled or missing logs cannot
be recovered after the fact. No shortened copy of the ZIP is substituted.
Review it before sharing because GitHub issues are public.

The original reporting service, Telegram membership requirement and anonymous
usage check-in are removed from this fork's app. Attribution remains in About
and the README. Original OneOS is by Fahad Ali Javed, with additional
contributors credited there.

## Payload hosting

`app-android/packages/remote.txt` points to the fork's `fork-payloads-v1` release.
GSI image downloads have their own sources and are outside this migration.
The signed startup-script feed remains in this fork's debug/release feed branches.

`scripts/migrate-patch-payloads.py` copies the pinned binary payloads. Historical
origins in `scripts/patch-migration/sources.json` are an audit and migration
input and are not packaged as app download sources. Every missing payload must
match its pinned size and SHA-256 before publication starts. Existing assets
must have matching size and GitHub SHA-256 metadata and are never overwritten.
A rerun resumes publication by skipping matching existing assets.

The Fold8 contributor snapshot is published to `fork-fold8-experimental-v1`.
The original channel snapshot had guarded files from an older app. The fork
snapshot retains this app's complete pinned file set and only replaces
`Fold8SystemUI.apk` and `GlobalVerifiedAddons.tar`, the two names the existing
channel verifier permits. Its guarded jars, binaries and `guards.txt` are the
app's. A cached older contributor manifest may be refused until the user checks
for channel updates. This Fold8 snapshot still needs a phone test.

The app requires acknowledgement of a compatibility warning on phones other
than `SM-S911*` (base S23) and `SM-S938*` (S25 Ultra). The text states these are
the only tested phones for this fork's patches and warns that other S and A
models are likely to fail. Other models remain experimental.
