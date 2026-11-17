# Prototype: One UI's albums in ColorOS (read-only)

ColorOS can show the albums of One UI's internal storage: photos and videos
you took or saved in One UI. Each One UI album shows as its own album in
ColorOS's Gallery. They are read-only: ColorOS can view them but cannot change
them. Sharing is off by default and works only with a ColorOS guest.

Up to PR #14 only `DCIM/Camera` and the screenshots were shared (tried on the
Galaxy S25 Ultra, session 92032e7ae892154c: both read-only in MediaProvider's
and Gallery's namespaces). Every album, and the choice of albums, is the
owner's decision after that test.

## Which folders are albums

The same rule in the OneOS app (`oneos_root.sh oneui-media albums`) and in
ColorOS (`guest_start.sh`); both scripts keep one list of extensions, and a
test checks that the two lists are the same.

- An album is a folder of One UI's internal storage (`/data/media/0`) that
  directly holds at least one file ending in `.jpg .jpeg .png .gif .webp
  .heic .heif .avif .bmp .dng .mp4 .m4v .3gp .3g2 .mkv .webm .mov .avi .ts`
  (any case). There is no limit on the number of albums. Inside an album
  every file is shown, not only those types.
- Never an album, and never looked into:
  - `Android/` and everything in it, `Android/media` too (owner's decision,
    for privacy). WhatsApp's media in `Android/media/com.whatsapp` is not
    shared.
  - any folder whose name starts with `.`, and everything in it;
  - a folder with a `.nomedia` file, and everything below it (One UI's
    MediaStore hides those too). A `.nomedia` in the storage root hides
    every album.
- The storage root itself is never an album, even with photos directly in
  it: sharing it would show everything. takeover.log says how many files
  are left out.
- A folder whose path has a newline or another control character is
  skipped.

Each album shows only the files directly in it. A subfolder (a child album,
an unticked or hidden folder) is covered by an empty read-only folder inside
the album, so nothing shows twice and nothing unticked leaks through its
parent. `Pictures/A` and `Pictures/A/B` are two albums.

## Album names in ColorOS

All albums are under `OneUI/` in ColorOS's internal storage, one folder per
album: `OneUI/Camera (One UI)`, `OneUI/Screenshots (One UI)`, ... The folder
name is the album name in Gallery.

- `<folder> (One UI)`.
- When two albums have the same folder name (`DCIM/Camera` and
  `Pictures/Camera`), they become `Camera (One UI, DCIM)` and
  `Camera (One UI, Pictures)`. Names that differ only in letter case count as
  the same.
- When the names still clash, ` 2`, ` 3`, ... is added in path order.

## Turning it on and off, and choosing albums

In the OneOS app on One UI, open **Settings** and turn on **One UI photos in
ColorOS**. The switch creates the root-only directory
`/data/local/tmp/oneos-oneui-media` (owner root, mode 0700); turning it off
removes that directory. (The old test album's marker,
`oneos-media-share-test`, is no longer read.)

**Choose albums**, right below the switch, lists One UI's albums (folder ·
folders above it · number of photos and videos), ticked when ColorOS shows
them. Untick an album and tap **Save** to hide it in ColorOS. It works with
the switch on or off. The choice is made only in One UI's OneOS app; ColorOS
has no setting for it.

- The app stores the albums you unticked, not the ones you kept: an album
  you make later in One UI is shown until you untick it.
- The list is `/data/local/tmp/oneos-oneui-media-excluded`, a root-only file
  (owner root, mode 0600) with one album path per line. Turning sharing off
  keeps it, so the choice is still there when you turn sharing on again.
  With nothing unticked, the file is removed.
- If that file is there but is not root's own 0600 regular file (a link, a
  folder, another owner or mode, more than 256 KiB), ColorOS shows **no**
  album: an unticked album must never show by mistake. Choose albums says
  why.
- Unticking an album does not hide the albums inside it; they have their own
  rows.
- Saving writes exactly the albums unticked in the list. An album that has no
  photos at the moment is not in the list, so saving drops it from the file;
  untick it again once it has photos.
- Changes apply at the next switch to ColorOS or the next guest restart.

## How it works

1. Before ColorOS starts, while One UI's data is still reachable,
   `guest_start.sh` pins One UI's whole `media/0` as one read-only bind
   (`ro,nosuid,nodev,noexec,noatime,nosymfollow`) in a root-only hold
   directory, copies the list of unticked albums into the hold, and makes an
   empty read-only cover directory there. The view of One UI's whole /data
   that it needs for this step is removed before the guest is cut off from
   One UI's mounts. Nothing is copied from the albums and nothing takes
   space.
2. Nothing is published at that point. In S25 Ultra session
   9a5159cc76ee3748 the test album was mounted early, and 4 s later
   ColorOS's own vold rebuilt the storage views. Apps saw only the empty
   mount point, and ColorOS wrote into it.
3. Next to the zygote start, a background job waits (up to 180 s) until
   ColorOS's vold has built its storage. It then lists One UI's albums in
   the hold, leaves out the unticked ones and names the rest.
4. For each album, all or nothing: the album is bound once inside the hold
   and the cover is bound over each of its subfolders. Then the album with
   its covers is bound with one `--rbind` onto `OneUI/<name>` on every view
   vold made of the guest's `/data/media/0`, such as
   `/mnt/pass_through/0/emulated/0`, which MediaProvider reads behind
   ColorOS's FUSE storage. So no view ever shows an album without its
   covers, even while MediaProvider scans. The flags, the album's identity
   and every cover (its flags, and that it is empty) are checked on every
   view, before and after propagation. If anything fails, the album's covers
   and mounts are removed again on every view (covers first) and only that
   album is skipped. Apps and MediaProvider get the mounts through mount
   propagation.
5. Once the albums are published (or skipped), the job removes the hold. The
   album mounts and covers made from it stay. After ColorOS has booted, it
   asks MediaProvider once to scan `OneUI/` (the scan goes into every
   folder), so ColorOS Gallery lists the files.

The albums are never chowned, chmodded or written. The guest's own recursive
`chown`/`chmod` of `/data/media` runs before the albums are mounted. A
ColorOS folder `OneUI/<name>` that already exists and has files in it is
never covered: that one album is skipped. If `OneUI` itself is a link, a file
or a mount, no album is published.

## What to check

- **Gallery** in ColorOS: does every One UI album you did not untick show
  as its own album, named `<folder> (One UI)`, with its thumbnails, photos
  and videos? Does no unticked album show, and nothing of WhatsApp or other
  apps' `Android/media`?
- **Files > Internal storage > OneUI**: each album shows only its own files;
  a subfolder shows as an empty folder.
- Copying one item to a ColorOS album works. Editing, renaming, moving,
  trashing or deleting a shared item must fail, and the item must still be
  in One UI afterwards.
- Untick an album, switch to ColorOS, and check it is gone; tick it again
  and check it is back. Make a new folder with a photo in One UI and check
  it shows in ColorOS at the next switch.
- Restart ColorOS once and check again. Turn sharing off, start ColorOS, and
  check that no album is shared.

## Reading takeover.log

Every line starts with `[Pivoted Root] Media prototype`. No line names a file
inside an album; album names do show (the log is the owner's own).

- `off: <reason>`: the marker exists but a gate check failed (marker not a
  root 0700 directory, not ColorOS, no private mount namespace, ...).
  Without the marker nothing is printed.
- `skipped: source missing or protected mount unavailable: <reason>` at
  boot: nothing is shared, for example because the kernel or busybox lacks
  `nosymfollow` (the line gives the actual mount options), One UI's media/0
  is a link, or the list of unticked albums is not root's own 0600 file
  (`the album exclusion list ... so no album is shared`).
- `the empty cover dir keeps its own SELinux label (...)`: the busybox could
  not read or set One UI's media label on the cover. Sharing goes on; a
  covered subfolder may then be unreadable to MediaProvider instead of
  empty.
- `One UI's media/0 pinned read-only (album exclusion list: N line(s));
  albums are published once the guest's vold has built its storage`.
- `the guest's vold built its storage views at HH:MM:SS (N s after the
  zygote launch), settled at HH:MM:SS: <views>`: when vold's views appeared
  and which views were used. `skipped: the guest's vold built no storage view
  of /data/media in 180 s (seen: ...)` means nothing was published.
- `One UI has N album(s) (listed in T s), U unticked by the owner, P to
  publish`; `K line(s) of the album exclusion list are not album paths and
  were ignored`; `K media file(s) directly in One UI's storage root are not
  shared`.
- `album k of N mounted as OneUI/<name> (F media files, C child dirs
  covered, A aliases)`: published.
- `skipped album k of N (OneUI/<name>): guest storage aliases could not be
  protected: <reason>`, then `rolled back OneUI/<name>: ...`: one view or
  cover failed a check. Every mount of that album and its covers was removed
  again, and so was the mount-point directory if it was empty.
- `skipped every album: <reason>`: `OneUI` in ColorOS is a link, a file or a
  mount.
- `albums: F found, E excluded, M mounted, S skipped`: the totals.
- `hold released`: the pinned copy is gone (the published albums stay).
- `OneUI is <mode uid gid>, OneUI/<first album> is <mode uid gid> (mode uid
  gid), one file in it is <mode uid gid>`: whether ColorOS's MediaProvider
  may read One UI's files.
- `MediaProvider <package> pid N: Uid ...; Gid ...; Groups ...` and
  `view MediaProvider pid N: ...` / `view <app> pid N: ...`: the storage
  mounts those processes see (device, root, mount point, options, type; the
  first 20). An album mount in MediaProvider's view means propagation
  reached it. Spaces in names are written `\040`, as the kernel does.
- `MediaStore lists K item(s) under OneUI/ before the scan request`: whether
  MediaProvider kept the rows of the last switch or indexes every album
  again at every boot. Then `scan of OneUI asked ... (am status 0, ...):
  Broadcast completed: result=0`, and `MediaStore lists K item(s) under
  OneUI/ 30 s after the scan request`.
- `FATAL: could not detach <path> even with umount -l; asking the host to
  return to One UI`: a mount that failed protection could not be removed.
  The session ends and the phone returns to One UI.

For mounts not covered by these lines, check with guest root:
`su -c 'grep OneUI /proc/self/mountinfo'`. The per-mount options (before the
` - ` separator) must contain `ro,nosuid,nodev,noexec,noatime,nosymfollow`.

## Known limits

- Root in ColorOS can remount the albums read-write. The read-only
  protection does not hold against guest root.
- ColorOS apps can read, copy and upload what they see. Check ColorOS
  Gallery's cloud backup and any sync apps before turning sharing on.
- Hidden files directly in a shared album are shown too, for example the
  `.trashed-...` files apps that use Android's trash leave in an album
  (One UI Gallery's own trash is under `Android/` and is not shared).
- Every album is one mount and every subfolder of an album one more, on each
  of vold's views, and each is copied into the apps' namespaces. With very
  many albums or subfolders that is many mounts; the log gives the counts.
- Listing the albums reads every folder name of One UI's storage (not
  `Android/`) at every ColorOS start, in the background; the log gives the
  time it took.
- The first scan of many photos and videos costs CPU time and heat for a
  while after ColorOS starts.
- After sharing is turned off, the empty `OneUI/...` mount-point folders stay
  in ColorOS. They are removed at the next start that has sharing on.
- The shell user (adb) of One UI can delete the list of unticked albums in
  `/data/local/tmp`; ColorOS then shows every album. That user can read all
  of One UI's photos anyway.
- Tested only in the fake-busybox test (`scripts/test-guest-media-share.py`)
  and the root script's tests. Every-album sharing, the covers and the album
  picker have not yet been tried on a phone.
