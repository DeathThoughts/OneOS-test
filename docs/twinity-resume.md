# Twinity4c ColorOS 17 guest build

The source is the OnePlus 13T ColorOS CN port for the Poco F5 (marble). Download
the four `OnePlus13T_ColorOS_CN_17.0.0.104_MARBLE_TWNTY4.0.zip.t0.00*`
assets from the [OneOS-test images release](https://github.com/DeathThoughts/OneOS-test/releases/tag/images)
into one directory. The source reports Android 17, SDK 37, codename `REL`,
build `CP2A.260605.016`, and security patch `2026-09-01`.

From the checkout root, run:

```sh
python3 scripts/build-twinity-image.py --source-dir /path/to/release-parts
```

The host needs `zstd` and a prepared MysticGSI checkout at
`825b850dd8ddff11fdb8ed024034fc00e94b1451` with its `.venv`. The work directory
defaults to `out/twinity-image`. Keep it on the same filesystem as MysticGSI
for the hardlinks. Add `--prepare-only` to extract verified partition images
without MysticGSI. The full command writes `system.img` and its provenance
manifest `system.json`. Keep the work directory for reuse. The builder checks
the release parts and verifies each cached file it uses.

The split files present one ZIP containing a stored inner ROM ZIP. The build
streams `images/super.img.zst` from the inner ZIP and verifies the raw super
image SHA-256. The pinned ranges come from its liblp metadata. Only `system`,
`system_ext`, `product`, and `odm` feed the MysticGSI ColorOS build with
debloating disabled. The Poco installer, boot images, firmware, and vendor
partitions are outside this build path.

A previous conversion produced a 10,238,480,384-byte ext4 image with SHA-256
`be85caf57da613678c4392f7aa73bacd0bfcd3f48266f47817970bdc044f1690`.
The conversion can produce a different digest on rebuild. The new manifest
records the digest of the image actually built. Guest boot on a phone remains
unverified.
