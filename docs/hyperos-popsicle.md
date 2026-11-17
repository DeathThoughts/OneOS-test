# HyperOS 4 on Galaxy S23

The trial targets the base Galaxy S23 (SM-S911, dm1q, Snapdragon 8 Gen 2)
running Android 17 One UI. It uses the Xiaomi 17 Pro Max (popsicle) China
build OS4.0.0.39.XPBCNXM, Android 17 build CP2A.260605.016. The existing
Xiaomi 18 Fold HyperOS image stays available alongside it. Popsicle has not
been tested on a phone.

## Import the image

1. Download `HyperOS.4.17.Pro.Max.zip.001` and
   `HyperOS.4.17.Pro.Max.zip.002` from the [images release](https://github.com/DeathThoughts/OneOS-test/releases/tag/images).
2. Join the two parts in numeric order. Extract the joined ZIP once to get
   `Hyperos_popsicle_17_17OS4_0_260909_233731301_QCPECN_S_AB_20260920.zip`.
3. In OneOS, choose **HyperOS 4** and pick that inner ZIP with `system.img`
   still inside. OneOS checks the archive and streams out the image.

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| Inner ZIP | 3,999,449,075 | `1603c41aceb505ae6c51349a51312e127433eb80cd5a297bbb49a6eb7e70caf9` |
| `system.img` | 7,203,971,072 | `a0d622cff955e907b89bf7691d6d12102587bf9333ad89e92aebee0fe57b12ba` |

## Payload build

`scripts/build-popsicle-payloads.py` verifies both release parts, the joined
archive, the inner ZIP and the raw image. `scripts/make-overlays.sh` selects
the Popsicle patch sites by that raw hash. The patch set covers Popsicle's
Xiaomi netd proxy and Wi-Fi HAL casts, both 32-bit SurfaceFlinger mode-group
comparisons, and a framework dex split that fits the Samsung IMS additions.
The staged set includes the existing OneOS guards, the guest vold as
`gvld`, the image's Google services APEX and the source pins for signal
polling. One UI supplies tethering. The image's wallpaper predicate already
handles an empty value, so it uses Xiaomi's original wallpaper APK.

The payload workflow built, tested and verified the published payloads.
[CI run 44171942992](https://github.com/DeathThoughts/OneOS-test/actions/runs/44171942992)

## Phone checks

- Boot HyperOS and verify the display, refresh-rate changes and touch.
- Check Wi-Fi, Bluetooth, audio, SIM registration, calls, SMS and camera.
- With a fingerprint enrolled in One UI, verify guest unlock and the guest
  PIN fallback.
- Use **Back to One UI**, then verify One UI display, touch and unlock.
