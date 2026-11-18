# ColorOS 17 China (Twinity4c) on Galaxy S23

The trial targets the base Galaxy S23 (SM-S911B, dm1q, Snapdragon 8 Gen 2)
running Android 17 One UI. It uses the OnePlus 13T ColorOS 17 China build
`OnePlus13T_ColorOS_CN_17.0.0.104_MARBLE_TWNTY4.0` (the Poco F5 "marble"
port), Android 17 build `CP2A.260605.016`, security patch 2026-09-01. It is
offered only on the base Galaxy S23; the S23+, S23 Ultra and au S23 do not
take it. It has not been tested on a phone.

## Import the image

1. Download `OneOS_ColorOS17_Twinity4c.zip.001`, `.002` and `.003` from the
   [Twinity4c release](https://github.com/DeathThoughts/OneOS-test/releases/tag/twinity4c-81fff7581ff4).
2. Join the parts in numeric order:
   `cat OneOS_ColorOS17_Twinity4c.zip.001 OneOS_ColorOS17_Twinity4c.zip.002 OneOS_ColorOS17_Twinity4c.zip.003 > OneOS_ColorOS17_Twinity4c.zip`
3. In OneOS, choose the **ColorOS 17 · China TWNTY4.0c** card, tap **I have
   the file**, and pick the joined ZIP. Leave `system.img` inside it; OneOS
   checks the archive and streams the entry out.

The joined ZIP holds exactly one entry, the stored `system.img` (checked in
CI). The app does not need the three parts separately.

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| Joined ZIP | 5,186,666,434 | `71ad5919cbb6d813b8815e6dc4b27790c23724ca4f7441eb2a5c1ace08fbdc4e` |
| `system.img` | 10,238,496,768 | `3d918b88d7ab4cf8f49854414d872f8c959d13d0a62e3f2691a5f5c9c0a57189` |

The split assets and their digests are in the release's `inventory.json`.

## Payload build

The image workflow verifies the source parts, the joined archive, the single
`system.img` entry and the raw image, then builds the S23 overlays with the
existing guards. The 29 files (424,083,730 bytes) are published as
content-addressed assets on the `fork-payloads-v1` release; the set list is
`app-android/packages/twinity-s23.sha256`, with eight guarded files.

[CI run 44228663954](https://github.com/DeathThoughts/OneOS-test/actions/runs/44228663954)

## Phone checks

No phone boot or performance test has happened. When one is available:

- Check the display, touch, Wi-Fi, mobile data, SMS and calls.
- Check the camera, speaker, microphone and Bluetooth audio.
- Check that the fingerprint and face enrolled in One UI unlock the guest.
- Check that Google apps are absent and that **Back to One UI** returns.
- Report what fails; do not describe the build as booting or smoother until a
  run has shown it.
