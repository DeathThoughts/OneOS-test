Set files of `x9u-in-1105-sultra` (ColorOS 17, Find X9 Ultra IN, November image),
each named by its SHA-256, the name the OneOS app downloads it under. Files larger
than GitHub's per-file limit are stored as `<sha256>.part-NN` and joined in order.

coloros-1105-r6: r5 with services_patched.jar re-patched in place by
patches/guest_media_mount_hold.py (StorageManagerService waits up to 15 s for
OneOS's One UI albums before MediaProvider's mount scan) and guards.txt
re-pinned; SystemUI_wave.dex and ColorPanoramicSystemUI.apk are r5's, unchanged.

The owner publishes them as the release named after this branch (the part after
`set-files/`) by running the "Publish set files" workflow by hand; the workflow
joins the parts and checks every file against the hash pinned in
`app-android/packages/x9u-in-1105-sultra.sha256` before anything is published.
