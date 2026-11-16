# Prototype: view a One UI test album inside ColorOS

Test on the base Galaxy S23 with the debug build. This covers just one album
in one direction. No directory scan, permission repair, or cloud sync is
triggered. If OPPO Gallery does not index the album, try its files directly
before reporting that result.

1. In **One UI**, use My Files to create **Internal storage → DCIM →
   OneOSShareTest**. Put copies of a disposable photo and video in it. Save
   the originals' hashes and metadata if you plan to test edit or delete.
2. From a root shell in One UI, opt in with
   `mkdir -m 700 /data/local/tmp/oneos-media-share-test`. If the marker
   already exists and you didn't create it, inspect it before proceeding.
3. Switch to ColorOS. Check **Gallery** and **Files → Internal storage →
   DCIM → OneOSShareTest**. Check thumbnails, playback, and copying to a
   guest album. Try editing, renaming, trashing, and deleting the test
   items as an ordinary Gallery user. The originals must stay intact.
4. Inspect the current guest's takeover log for **Media prototype**. The
   message says whether the boot mounted the album or skipped it. Check
   exact album mounts in the guest namespace with
   `su -c 'grep OneOSShareTest /proc/self/mountinfo'` when guest root is
   enabled. The per-mount flags before the separator should contain
   `ro,nosuid,nodev,noexec,noatime,nosymfollow` in any order.
5. Restart the guest once and check the album again. Return to One UI and
   compare the original hashes and metadata. Disable sharing with
   `rmdir /data/local/tmp/oneos-media-share-test` from a One UI root shell,
   then check that the next ColorOS session has no test album mount. The
   local empty album directory can remain after a session.

The album contains files from One UI; guest apps can still read or copy
any files their permissions allow. Use disposable files and consider
Gallery backup settings on the guest before sharing. Guest root is outside
the read-only protection guarantee. A missing source, conflicting guest
album, missing alias, or unsupported mount protection skips sharing. An
insufficiently protected mount that cannot be detached aborts guest start.
