#!/usr/bin/env python3
"""HyperOS's SurfaceFlinger with the display mode group read whole.

Xiaomi's libsurfaceflinger.so compares a mode's group with the default (or
active) mode's group, loading one side as 32 bits and the other as one byte
(ldr w, [x, #48] against ldrb w, [x, #48]). One UI's composer numbers the groups
small on the S25 family (0..7), which a byte holds, and by the panel width on
the S23, S24 Ultra and Z Flip5 (720, 1080, 1440): 1440 against its low byte 160
never matched, constructAvailableRefreshRates() found no mode and SurfaceFlinger
aborted at the first display ("No matching frame rate modes ... even after
ignoring the render range" with an unrestricted policy), a black screen every
HyperOS session there (0.4.31 to 0.4.36). Both byte loads become 32-bit loads.

Each supported /system_ext/lib64/libsurfaceflinger.so is pinned by SHA-256.

Usage: hyperos_sf_group.py <libsurfaceflinger.so> <out libsurfaceflinger_patched.so>
"""
import hashlib
import sys

# (file offset, original instruction, patched instruction); offsets equal the
# addresses in these libraries (their text segments are mapped at their file offsets).
BUILDS = {
    # Xiaomi 18 Fold (lhasa).
    "b2f2d5d88f91f64dcaab820a2a44e86e30e63e01d201b2465c10ce8388e0db77": (
        # constructAvailableRefreshRates()::$_2 filter: ldrb w8, [x8, #48] -> ldr w8, [x8, #48]
        (0x4880B0, "08c14039", "083140b9"),
        # the same group comparison in RefreshRateSelector (active mode): ldrb w9, [x21, #48] -> ldr w9, [x21, #48]
        (0x477328, "a9c24039", "a93240b9"),
    ),
    # HyperOS OS4.0 Popsicle OS4.0.0.39.XPBCNXM.
    "54c8cd4021824244f717759c5bf20a9cee9f4b56296213d9a05e5f0c35605089": (
        # getRankedFrameRatesLocked(): ldrb w9, [x23, #48] -> ldr w9, [x23, #48]
        (0x4A1900, "e9c24039", "e93240b9"),
        # constructAvailableRefreshRates() filter: ldrb w8, [x8, #48] -> ldr w8, [x8, #48]
        (0x4B3210, "08c14039", "083140b9"),
    ),
    # HyperOS OS4.0.5.0.XFRMIXM Global (hongkong)
    "795d4a96c32007a612fc62956fabd40fb2c279b4ddb83e625c122d633239c8ad": (
        # constructAvailableRefreshRates()::$_2 filter (std::function body): ldrb w8, [x8, #48] -> ldr w8, [x8, #48]
        (0x4B7EE4, "08c14039", "083140b9"),
        # getRankedFrameRatesLocked, active mode's group: ldrb w9, [x23, #48] -> ldr w9, [x23, #48]
        (0x4A65D4, "e9c24039", "e93240b9"),
    ),
}


def main():
    src, out = sys.argv[1], sys.argv[2]
    data = bytearray(open(src, "rb").read())
    have = hashlib.sha256(data).hexdigest()
    patches = BUILDS.get(have)
    if patches is None:
        sys.exit("not the library this patch was made for (sha256 %s)" % have)
    for off, old, new in patches:
        if data[off:off + 4] != bytes.fromhex(old):
            sys.exit("unexpected bytes at %#x: %s" % (off, data[off:off + 4].hex()))
        data[off:off + 4] = bytes.fromhex(new)
    open(out, "wb").write(data)
    print("wrote %s (%d group loads made 32-bit)" % (out, len(patches)))


if __name__ == "__main__":
    main()
