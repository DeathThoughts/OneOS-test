# Experimental guest partition images

This branch adds optional raw ext4/EROFS images alongside the existing patched
`system.raw`. It does not flash partitions. Existing installations with no
`partition_images` directory take the existing path.

## Implemented and blocked

| Image | Bundle verification | Guest activation |
| --- | --- | --- |
| `product`, `system_ext` | SHA-256, size, filesystem, root ownership | Optional read-only loop mount before framework patches |
| `vendor` | Same, plus exact audited S23 profile and live block-device proof | Optional `stock` mode only; donor activation refused |
| `odm` | Same | Refused |
| `vendor_dlkm`, `odm_dlkm`, `system_dlkm` | Same | Refused; no module loading |
| `boot`, `init_boot`, `vendor_boot`, `dtbo`, `vbmeta`, `super`, modem/bootloader | Refused | Refused |

Verification-only images are **not mounted** and have no runtime effect. There
is no performance gain from merely adding them to a bundle. This is groundwork
for a device-specific port, not a completed ColorOS vendor port for the S23.

The current runtime keeps Samsung's kernel and vendor services alive. Guest
framework calls still reach those services. Replacing files under the guest's
`/vendor` would not replace host init's service binaries, loaded drivers, Binder
registrations or linker state. A replacement needs a separate design for these
shared dependencies and measured device tests. Do not enable donor OnePlus/Poco
vendor services or kernel modules on a Samsung based on this loader.

## Build a bundle offline

Use framework partition images extracted from the **same port build**, and the exact
patched `system.raw` currently staged by OneOS. Vendor stock mode is a separate
case: it uses the audited Samsung image matching the running host, not a donor vendor. Do not use a stock product or
system_ext from a different firmware with an existing patched framework.

```sh
python3 scripts/prepare-guest-partitions.py \
  --system /path/to/system.raw \
  --image product=/path/to/product.img \
  --image system_ext=/path/to/system_ext.img \
  --image vendor=/path/to/vendor.img \
  --image odm=/path/to/odm.img \
  --output /path/to/partition_images
```

All images default to verification only. To request experimental framework
mounts, add `--activate product --activate system_ext`. The tool performs no
phone operations. It refuses sparse images; convert them with `simg2img` first.
Filesystem magic checks at packaging time are a preliminary check; the runtime
also probes the filesystem with BusyBox blkid before any loop attachment.

The bundle must be installed as a real root-owned directory at
`/data/local/tmp/oneos/partition_images`, mode 700, with its files root-owned and
mode 600, only while OneOS is idle. This developer path has no app import UI;
normal APK setup does not install bundles. Use an APK containing the changed
scripts; do not mix updated and older staged helpers. No on-device installation
or activation has been performed as part of implementing this change.

## Checks and recovery limits

- Before One UI is stopped, malformed manifests, unsupported activation,
  unexpected filesystem types, unsafe links/permissions, wrong sizes, changed
  image hashes or a different base system are refused.
- At prepare time all checks run again. Only known guest paths are accepted;
  absolute symlinks in images must never resolve into One UI's partitions.
- Mounts use read-only loop devices, `nodev,nosuid`, and ext4 `noload` (no journal
  replay). Propagation is made private and checked. Failed operations attempt
  to unmount only this attempt's mounts and detach its loops.
- The existing return path removes mounts under the stage and detaches its
  loops. No separate persistent boot hook, real-partition write or driver load
  is added. Removing the optional bundle while idle disables this path.
- Existing guards/patch payloads must still match the activated framework.
  Hash pairing prevents accidental mixing of files, not an incompatible set
  deliberately packaged together. A bad framework can fail to start; existing
  watchdog/return behavior applies. Shared kernel/HAL access means **no promise
  of zero brick or data-loss risk** is possible.

## Validation status

Host regression tests exercise bundle creation, tampering, forbidden activation,
path escapes, and failure cleanup using a recorded mount/loop backend. They do
not validate Android filesystem mounting, SELinux, ColorOS compatibility or
fluency on real hardware. A phone test and a matching adapted image set remain
required before this can be promoted to release.

## Vendor investigation (second stage)

`oneos_parts_inspect_vendor` now implements a separate, developer-only vendor
image mount. It is **not** called by switch or prepare. The launcher is
`scripts/inspect-guest-vendor.sh`; it must be invoked as root while the stage is
idle, with an updated staged `guest_partitions.sh` and a verified bundle
containing `vendor` in `inspect` mode.

The launcher creates a mount namespace with BusyBox `unshare -m`. The helper
refuses init's namespace, makes propagation private, mounts a small temporary
filesystem under the stage, and attaches `vendor.img` through a read-only loop.
The image is mounted `ro,nodev,nosuid,noexec` (ext4 also `noload`). It emits a tar
archive of properties, init/VINTF/linker/SELinux config files to stdout, then
unmounts and detaches its loop. Neither `/vendor` nor the guest's vendor mount
is replaced. Image symlinks are not followed. No donor service, property file,
SELinux policy, executable or kernel module is loaded.

The archive output is binary. Redirect stdout to a file outside the stage;
keep stderr separate. The command has not been run on the S23. Mounting an
untrusted filesystem still exercises the shared kernel's filesystem parser;
namespace isolation does not eliminate that risk. Cleanup handles normal exits
and HUP/INT/TERM; SIGKILL or power loss may leave a loop until reboot. Do not
run the inspector concurrently with OneOS setup, switch or return.

`scripts/audit-guest-vendor.py` accepts the config archive or an already extracted
vendor directory and emits JSON to stdout:

```sh
python3 scripts/audit-guest-vendor.py --stock /path/to/stock-vendor \
  --candidate /path/to/donor-vendor > vendor-audit.json
```

It inventories VINTF HAL versions/instances, init service definitions and import
references, selected build properties, and config hashes. It reports differing
HAL contracts and colliding service names. It never executes init imports,
extracts archives to disk, starts a service or certifies activation. It preserves
parse failures as explicit gaps. ELF/library dependencies, actual Binder
behavior, kernel ABI, SELinux compatibility and dynamic init imports remain
unverified.

The owner supplied the stock vendor and ODM images plus the matching runtime
fingerprint. Both image hashes and extracted properties were verified. See
`docs/vendor-s23-stock-audit.md` for the actual interfaces and evidence. No donor
vendor service is enabled, and no speedup or successful phone test is claimed.

The first APK CI attempt (`45334153324`) passed the new loader checks and built
the guest manager, then failed two existing patch-migration tests. Both failures
were reproduced on unchanged base `cc6f325`; they concern payload source/snapshot
pins, not partition mounting. They remain a separate build blocker; this work
does not bypass those checks.


## S23 stock vendor guest mount (third stage)

`--stock-vendor` enables manifest mode `stock` for vendor only. It accepts the
exact EROFS image audited in `vendor-s23-stock-audit.md` (SM-S911B GZIF, SHA-256
3ace2530806e1eeba86007eab46a4fbebe607caf04ea73ce6cbb2ec6a17c63e7,
1907937280 bytes). It does not permit a modified image, another Samsung firmware,
a Poco/OnePlus vendor or any ODM/dlkm activation.

```sh
python3 scripts/prepare-guest-partitions.py \
  --system /path/to/current-patched-system.raw \
  --image vendor=/path/to/vendor-stock.img \
  --stock-vendor --output /path/to/partition_images
```

Before stopping One UI, and again before mounting, the runtime checks the
fingerprint, vendor model SM-S911B and board kalama. It parses the *actual*
`/vendor` mount's device/major/minor, requires one plain EROFS root mount, checks
the block device's byte size, and hashes the entire live block device read-only.
Its digest must equal the supplied image. Root overlays or ambiguous mounts fail
closed rather than selecting a guessed partition/slot.

The guest receives a read-only loop mount of that same stock content at its real
`/vendor` directory. The default host-vendor bind is skipped only after this mount
succeeds. Existing firmware/APEX/module submounts under host `/vendor` are rebound
at canonical matching guest paths. Parent subtrees carry their descendants with
rbind; each bind is made rslave/private immediately. Unsafe links, absent targets,
changed types and excessive submount counts abort. Before rollback unmounts any
tracked subtree it must make it recursively private; if isolation fails, cleanup
stops rather than risking host mount propagation. Only that attempt's targets and
loops are unwound. The usual stage-return cleanup also covers the vendor loop.

Samsung's host services, Binder registrations, linker configuration and drivers
remain in use. No donor rc/property/policy is loaded. This is a vendor-image
loading baseline using byte-identical stock content, **not an OPlus HAL adapter**
and not an expected performance improvement by itself. The file/manifest checks
also remain paired to the exact patched guest system.raw. Extra hashing adds
preflight/startup work.

The feature defaults off, has no app import UI yet, and has not been run on a
phone. Tests use small fixture images with recorded mount operations; production
profile pins are tested separately without relaxing them in runtime code. A phone
test of the opt-in path and normal return is still required before promotion.
