Set files of `x9u-in-1105-sultra` (ColorOS 17, Find X9 Ultra IN, November image),
each named by its SHA-256, the name the OneOS app downloads it under. Files larger
than GitHub's per-file limit are stored as `<sha256>.part-NN` and joined in order.

coloros-1105-r7: services_patched.jar is r6's re-patched in place with upstream
0.5.20's guest lock handle and placeholder token (guest_local_lock_hat.py) and its
optical-sensor helpers (guest_udfps_location, guest_seh_touch, guest_fp_touchwatch);
ColorPanoramicSystemUI.apk carries our fingerprint wave and panoramic AOD plus
upstream's four lock screen and AOD patches; SystemUI_wave.dex is r5's; guards.txt
re-pinned.

The owner publishes them as the release named after this branch (the part after
`set-files/`) by running the "Publish set files" workflow by hand; the workflow
joins the parts and checks every file against the hash pinned in
`app-android/packages/x9u-in-1105-sultra.sha256` before anything is published.
