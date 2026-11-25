# S23 stock vendor: initial offline audit

Source: owner-provided `vendor-stock.img` and `odm-stock.img`, public release
`DeathThoughts/OneOS-test`, tag `images`, downloaded and audited 2026-11-24.
No images or proprietary binaries are added to this repository.

| Image | Bytes | SHA-256 |
| --- | ---: | --- |
| vendor-stock.img | 1907937280 | 3ace2530806e1eeba86007eab46a4fbebe607caf04ea73ce6cbb2ec6a17c63e7 |
| odm-stock.img | 778240 | 1c1e9e3f95fa3e635dcfbb63e37a76fc40f60014b6a6bc8c8ed7e1457d29f4de |

Both hashes match GitHub's asset digests. Both images are EROFS and were
successfully decoded with fsck.erofs 1.8.6 in userspace, without mounting them
or running code from them. No phone operations were performed.

Vendor and ODM build properties both match the fingerprint supplied by the
owner from the running phone:

```
samsung/dm1qxxx/dm1q:13/TP1A.220624.014/S911BXXUAGZIF:user/release-keys
```

Vendor identifies SM-S911B, device dm1q, board kalama, VNDK 33 and vendor SDK
33; its security patch property is 2026-10-05. These are vendor-side metadata,
not proof that the currently running system framework is Android 13.

## Actual interfaces and service paths

The config audit sees 156 HAL declarations, 242 init service definitions and
228 selected configuration files. These counts include variant declarations;
they are not counts of services confirmed running. Init imports and property
conditions are not evaluated. After supporting continued init lines (the stock
wpa_supplicant definition uses them), no selected config has a parser error.

| Component | Image evidence |
| --- | --- |
| Display | android.hardware.graphics.composer 2.4, HIDL; vendor.qti.hardware.display.composer-service |
| Audio | android.hardware.audio 7.1 and audio.effect 7.0, HIDL; variant audio service definitions |
| Samsung audio | ISecAudioHal AIDL v1 plus Samsung HIDL audio additions |
| Bluetooth audio | vendor.samsung.hardware.bluetooth.audio, ISehBluetoothAudioProviderFactory/default |
| Power | android.hardware.power IPower/default AIDL v2; android.hardware.power.samsung-service |
| HYPER | vendor.samsung.hardware.hyper ISehHyPer/default AIDL v2; vendor.samsung.hardware.hyper-service |
| Thermal | Android thermal HIDL 1.0/2.0 plus Samsung thermal 1.0 |

HYPER's ELF dynamic table directly names libhyper.so and
vendor.samsung.hardware.hyper-V2-ndk.so, alongside base, utils, cutils,
hardware, log, binder_ndk and C/C++ runtime libraries. Reading DT_NEEDED is
not a complete symbol/ABI or linker namespace compatibility check. No binary
was executed and no interface behavior is certified by this inventory.

The ODM contains ten regular files: build properties, NOTICE, filesystem
ownership/group metadata and precompiled SELinux policy plus mapping hashes.
Its small size is consistent with its actual content. It does not supply a
second large collection of hardware libraries in this dump. Its SELinux
policy is not loaded by this work.

## Consequences for the implementation

Keep Samsung's display, audio, Bluetooth, power/HYPER and thermal services as
the hardware owners while developing explicit guest-side adapters. A donor
OPPO vendor image cannot be substituted based on matching partition names or
SoC family. The guest framework must agree with the actual HIDL/AIDL contracts
and the existing shared service managers.

Next framework inputs are `system_ext` and `product` from the same intended
guest build (and stock versions only when studying Samsung dependencies).
`vendor_boot` is a boot image with vendor ramdisks, not a filesystem image to
mount as another guest framework partition. Offline examination could inform
init/fstab/module dependencies, but this work neither flashes it nor applies
its boot-time content to the running host. Donor vendor activation remains blocked. A separate opt-in loader now accepts
only these exact stock vendor and ODM images, proves it matches the live host block device and
preserves the corresponding host submounts. This code has not been validated on a phone;
it is a loading baseline, not a completed ColorOS vendor port.
