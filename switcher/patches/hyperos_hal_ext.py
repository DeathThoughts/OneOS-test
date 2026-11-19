"""HyperOS's casts of HAL extension binders, against One UI's HALs.

Xiaomi's framework asks AOSP HAL binders for their vendor extension and casts
it to a Xiaomi interface without checking it:

    IBinder ext = supplicant.asBinder().getExtension();
    mISupplicantXm = ISupplicantXm.Stub.asInterface(ext);

asInterface does not check the remote descriptor. In a session those HALs are
One UI's, whose extension carries Samsung's interface, so every Xiaomi call on
it was rejected (BAD_TYPE, IllegalArgumentException), and an uncaught one took
system_server down (Z Fold8 Ultra, 2026-10-25: MiuiWifiService,
ISupplicantXm.registerCallback). A Xiaomi transaction could also land on one
of Samsung's methods.

Each such cast now goes through a helper that passes the extension on only
when its descriptor is the target interface's DESCRIPTOR (read at run time),
and null otherwise; Xiaomi's code already handles a null interface (for
example "mISupplicantXm is null"). The helper class is added to the jar.

Every getExtension() call in the jar must be such a cast, and their number
must be the expected one, or nothing is written (fail closed).

Usage: hyperos_hal_ext.py [--hongkong] --expect N <apktool decoded jar directory> <helper class name>
Prints the smali_* directories (dex files) it changed.
"""
import hashlib
import os
import re
import sys

CAST = re.compile(
    r"(    invoke-interface \{(v\d+)\}, Landroid/os/IBinder;->getExtension\(\)Landroid/os/IBinder;\n"
    r"\n    move-result-object \2\n)"
    r"(\n    invoke-static \{\2\}, L([\w/$]+)\$Stub;->asInterface\(Landroid/os/IBinder;\)L\4;\n)")
CALL = "invoke-interface {"
EXT = "Landroid/os/IBinder;->getExtension()Landroid/os/IBinder;"

# The sixth cast in the Popsicle Wi-Fi jar checks for null between getExtension
# and asInterface. Match the audited method before inserting the guard there.
POPSICLE_WIFI_CLASS = "com/android/server/wifi/WifiOptimizationImpl.smali"
POPSICLE_WIFI_IFACE = "com/xiaomi/net/ip/IMiuiIpClientExtension"
POPSICLE_WIFI_METHOD = re.compile(
    rf"^\.method private getMiuiExtension\(Landroid/os/IBinder;\)L{POPSICLE_WIFI_IFACE};\n"
    r".*?^\.end method(?=\r?$)", re.M | re.S)
POPSICLE_WIFI_METHOD_SHA256 = "5dad924e68c4d09b68323f22b71ffa54a67030f70c34be4ced6c51c294de73af"
POPSICLE_WIFI_CAST = (f"    invoke-static {{v2}}, L{POPSICLE_WIFI_IFACE}$Stub;->asInterface"
                      f"(Landroid/os/IBinder;)L{POPSICLE_WIFI_IFACE};\n")

HELPER_METHOD = """
.method public static {name}(Landroid/os/IBinder;)Landroid/os/IBinder;
    .locals 2
    .param p0, "ext"    # Landroid/os/IBinder;

    if-eqz p0, :cond_none

    :try_start_0
    invoke-interface {{p0}}, Landroid/os/IBinder;->getInterfaceDescriptor()Ljava/lang/String;

    move-result-object v0
    :try_end_0
    .catch Ljava/lang/Exception; {{:try_start_0 .. :try_end_0}} :cond_none

    sget-object v1, L{iface};->DESCRIPTOR:Ljava/lang/String;

    invoke-virtual {{v1, v0}}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z

    move-result v1

    if-eqz v1, :cond_other

    return-object p0

    :cond_other
    const-string v1, "OneOSHalExt"

    invoke-static {{v1, v0}}, Landroid/util/Log;->i(Ljava/lang/String;Ljava/lang/String;)I

    :cond_none
    const/4 v0, 0x0

    return-object v0
.end method
"""


def main(argv):
    hongkong = argv[:1] == ["--hongkong"]
    if hongkong:
        argv = argv[1:]
    if len(argv) != 4 or argv[0] != "--expect" or not argv[1].isdigit():
        sys.exit(__doc__)
    expect, root, helper = int(argv[1]), argv[2], argv[3]
    if not re.fullmatch(r"[A-Z]\w*", helper):
        sys.exit(f"hyperos_hal_ext: bad helper class name {helper}")
    helper_cls = f"com/oneos/guest/{helper}"
    files = {}
    for d, _, fs in os.walk(root):
        for f in fs:
            if f.endswith(".smali"):
                p = os.path.join(d, f)
                with open(p, encoding="utf-8", newline="") as source:
                    raw = source.read()
                # The adjacent cast matcher still uses normalized newlines.
                s = raw.replace("\r\n", "\n").replace("\r", "\n")
                if EXT in s:
                    files[p] = (s, raw)
    ifaces, total, out = {}, 0, {}

    def descriptor_check(iface, reg):
        name = ifaces.setdefault(iface, f"ext{len(ifaces)}")
        return (f"\n    invoke-static {{{reg}}}, L{helper_cls};->{name}(Landroid/os/IBinder;)Landroid/os/IBinder;\n"
                f"\n    move-result-object {reg}\n")

    def sub(m):
        return m.group(1) + descriptor_check(m.group(4), m.group(2)) + m.group(3)

    for p, (s, raw) in sorted(files.items()):
        # Not a HAL: WifiOptimizationImpl.getMiuiExtension asks the IpClient binder of the guest's own NetworkStack for
        # IMiuiIpClientExtension (HyperOS OS4.0.5.0 Global, 11-19). Skipped only in that exact shape.
        if hongkong and p.endswith("/com/android/server/wifi/WifiOptimizationImpl.smali") and s.count(EXT) == 1 \
                and "Lcom/xiaomi/net/ip/IMiuiIpClientExtension$Stub;->asInterface" in s and not CAST.findall(s):
            print("hyperos_hal_ext: WifiOptimizationImpl's IpClient extension left as it is (the guest's own NetworkStack)", file=sys.stderr)
            continue
        calls = s.count(EXT)
        casts = len(CAST.findall(s))
        relative = os.path.relpath(p, root)
        popsicle_method = None
        if (expect == 6 and calls == casts + 1
                and relative.partition(os.sep)[2] == POPSICLE_WIFI_CLASS):
            methods = POPSICLE_WIFI_METHOD.findall(raw)
            if len(methods) != 1:
                sys.exit(f"hyperos_hal_ext: {relative}: Popsicle getMiuiExtension() not found exactly once")
            popsicle_method = methods[0]
            if hashlib.sha256(popsicle_method.encode("utf-8")).hexdigest() != POPSICLE_WIFI_METHOD_SHA256:
                sys.exit(f"hyperos_hal_ext: {relative}: Popsicle getMiuiExtension() fingerprint mismatch")
            if popsicle_method.count(EXT) != 1 or popsicle_method.count(POPSICLE_WIFI_CAST) != 1:
                sys.exit(f"hyperos_hal_ext: {relative}: unexpected Popsicle getMiuiExtension() cast")
            casts += 1
        if calls != casts:
            sys.exit(f"hyperos_hal_ext: {relative}: {calls} getExtension() calls, {casts} of them casts")
        patched = CAST.sub(sub, s)
        if popsicle_method is not None:
            guarded = popsicle_method.replace(
                POPSICLE_WIFI_CAST, descriptor_check(POPSICLE_WIFI_IFACE, "v2") + "\n" + POPSICLE_WIFI_CAST, 1)
            patched = patched.replace(popsicle_method, guarded, 1)
        out[p] = patched
        total += calls
    if total != expect:
        sys.exit(f"hyperos_hal_ext: {total} extension casts, expected {expect}")
    helper_path = os.path.join(root, "smali", *helper_cls.split("/")) + ".smali"
    if os.path.exists(helper_path):
        sys.exit(f"hyperos_hal_ext: {helper_cls} already exists")
    for p, s in out.items():
        open(p, "w").write(s)
    os.makedirs(os.path.dirname(helper_path), exist_ok=True)
    body = [f".class public final L{helper_cls};", ".super Ljava/lang/Object;", '.source "hyperos_hal_ext.py"', ""]
    for iface, name in ifaces.items():
        body.append(HELPER_METHOD.format(name=name, iface=iface))
        print(f"{helper}.{name}: {iface.replace('/', '.')} extensions only", file=sys.stderr)
    open(helper_path, "w").write("\n".join(body))
    for d in sorted({os.path.relpath(p, root).split(os.sep)[0] for p in list(out) + [helper_path]}):
        print(d)


if __name__ == "__main__":
    main(sys.argv[1:])
