# Prototype: One UI's camera and screenshots in ColorOS (read-only)

ColorOS can show the photos and screenshots you took in One UI. They are
read-only: ColorOS can view them but cannot change them. Sharing is off by
default and works only with a ColorOS guest.

| One UI folder (Internal storage) | In ColorOS |
| --- | --- |
| `DCIM/Camera` | `DCIM/OneUI_Camera` |
| `DCIM/Screenshots` | `DCIM/OneUI_Screenshots` |
| `Pictures/Screenshots`, only when One UI has no `DCIM/Screenshots` | `Pictures/OneUI_Screenshots` |

Each folder is shared on its own. If one is missing in One UI or fails a
check, only that one is skipped and the other is still shared.

## Turning it on and off

In the OneOS app on One UI, open **Settings > Advanced** and turn on sharing
One UI's camera and screenshots. The switch creates the root-only directory
`/data/local/tmp/oneos-oneui-media` (owner root, mode 0700); turning it off
removes that directory. The change applies from the next ColorOS start.
(The old test album's marker, `oneos-media-share-test`, is no longer read.)

## How it works

1. Before ColorOS starts, while One UI's data is still reachable,
   `guest_start.sh` pins each One UI folder as its own read-only bind
   (`ro,nosuid,nodev,noexec,noatime,nosymfollow`) in a root-only hold
   directory. The view of One UI's whole /data that it needs for this step
   is removed before the guest is cut off from One UI's mounts.
2. Nothing is published at that point. In S25 Ultra session
   9a5159cc76ee3748 the test album was mounted early, and 4 s later
   ColorOS's own vold rebuilt the storage views. Apps saw only the empty
   mount point, and ColorOS wrote into it.
3. Next to the zygote start, a background job waits (up to 180 s) until
   ColorOS's vold has built its storage. It then binds each pinned folder
   onto every view vold made of the guest's `/data/media/0`, such as
   `/mnt/pass_through/0/emulated/0`, which MediaProvider reads behind
   ColorOS's FUSE storage. It checks the flags and the folder's identity on
   every view. Apps and MediaProvider get the mount through mount
   propagation.
4. Once the folders are published (or skipped), the job removes the hold.
   After ColorOS has booted, it asks MediaProvider to scan each shared
   folder, so ColorOS Gallery lists the files.

The folders are never chowned, chmodded or written. The guest's own
recursive `chown`/`chmod` of `/data/media` runs before the folders are
mounted. A ColorOS folder that already exists and has files in it (for
example `DCIM/OneUI_Camera` with ColorOS's own photos) is never covered: that
one album is skipped.

## What to check

- **Gallery** and **Files > Internal storage > DCIM** in ColorOS: do
  `OneUI_Camera` and `OneUI_Screenshots` appear, and do thumbnails, photos
  and videos open?
- Copying one item to a ColorOS album works. Editing, renaming, moving,
  trashing or deleting a shared item must fail, and the item must still be
  in One UI afterwards.
- Restart ColorOS once and check again. Turn sharing off, start ColorOS, and
  check that the folders are no longer shared.

## Reading takeover.log

Every line starts with `[Pivoted Root] Media prototype`. No line names a
file inside a shared folder.

- `off: <reason>`: the marker exists but a gate check failed (marker not a
  root 0700 directory, not ColorOS, no private mount namespace, ...).
  Without the marker nothing is printed.
- `skipped <album>: source missing or protected mount unavailable: <reason>`
  at boot: that One UI folder is missing, is a symlink, or could not be
  pinned with the flags. `skipped: ...` without an album name means the
  whole share was skipped, for example because the kernel or busybox lacks
  `nosymfollow` (the line gives the actual mount options).
- `pinned read-only, published once the guest's vold has built its storage:
  camera:DCIM/Camera:DCIM/OneUI_Camera ...`: the folders that wait for vold.
- `the guest's vold built its storage views at HH:MM:SS (N s after the
  zygote launch), settled at HH:MM:SS: <views>`: when vold's views appeared
  and which views were used. `skipped: the guest's vold built no storage view
  of /data/media in 180 s (seen: ...)` means nothing was published.
- `<album> (One UI's <source>) mounted read-only as <target> (N aliases:
  ...)`: published.
- `skipped <album>: guest storage aliases could not be protected: <reason>`,
  then `rolled back <album>: ...`: one view failed a check. Every mount of
  that album was removed again, and so was the mount-point directory if it
  was empty.
- `hold released`: the pinned copies are gone.
- `<target> is <mode uid gid> (mode uid gid), K regular file(s), one is
  <mode uid gid>`: whether ColorOS's MediaProvider may read One UI's files.
- `MediaProvider <package> pid N: Uid ...; Gid ...; Groups ...` and
  `view MediaProvider pid N: ...` / `view <app> pid N: ...`: the storage
  mounts those processes see (device, root, mount point, options, type). An
  album mount in MediaProvider's view means propagation reached it.
- `scan of <target> asked ... (am status 0, ...): Broadcast completed:
  result=0`, then `MediaStore lists K item(s) under <target>/ 30 s after the
  scan request`: the scan request, and how many items MediaStore has for the
  folder.
- `FATAL: could not detach <path> even with umount -l; asking the host to
  return to One UI`: a mount that failed protection could not be removed.
  The session ends and the phone returns to One UI.

For mounts not covered by these lines, check with guest root:
`su -c 'grep OneUI_ /proc/self/mountinfo'`. The per-mount options (before
the ` - ` separator) must contain `ro,nosuid,nodev,noexec,noatime,nosymfollow`.

## Known limits

- Root in ColorOS can remount the folders read-write. The read-only
  protection does not hold against guest root.
- ColorOS apps can read, copy and upload what they see. Check ColorOS
  Gallery's cloud backup and any sync apps before turning sharing on.
- The first scan of many photos and videos costs CPU time and heat for a
  while after ColorOS starts.
- After sharing is turned off, the empty `OneUI_Camera` and
  `OneUI_Screenshots` mount-point folders stay in ColorOS. They are removed
  at the next start that has sharing on.
- Tested so far only in the fake-busybox test
  (`scripts/test-guest-media-share.py`). It has not yet been tried on a
  phone.
