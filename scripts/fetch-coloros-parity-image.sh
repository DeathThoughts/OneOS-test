#!/usr/bin/env bash
# Retrieve the image currently pinned by Images.kt, not a new OTA build.
set -euo pipefail
image=$1
out=$2
mkdir -p "$out"
out=$(realpath "$out")
cd "$out"
fetch() {
    curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' --retry 3 --connect-timeout 30 --max-time 1800 --output "$2" "$1"
}
check() {
    test "$(stat -c %s "$1")" = "$2"
    printf '%s  %s\n' "$3" "$1" | sha256sum --strict --check
}
case "$image" in
    findn6)
        fetch 'https://sourceforge.net/projects/oneos-mirror/files/images/OneOS_ColorOS17_FindN6_Global.img.gz/download' archive.gz
        check archive.gz 7213271568 b3061160ab3fdb37cd491e94c05e41238898320c1a5efb016debabc20991f3ee
        gzip -dc archive.gz > system.img
        rm archive.gz
        check system.img 14829686784 eec6c10581657b17f3164d6f964c749b351c4a7ec07233840da5f6a0ce28a47c
        ;;
    twinity)
        url=https://github.com/DeathThoughts/OneOS-test/releases/download/twinity4c-81fff7581ff4
        for suffix in 001 002 003; do
            fetch "$url/OneOS_ColorOS17_Twinity4c.zip.$suffix" "part.$suffix"
        done
        cat part.001 part.002 part.003 > archive.zip
        rm part.001 part.002 part.003
        check archive.zip 5186666434 71ad5919cbb6d813b8815e6dc4b27790c23724ca4f7441eb2a5c1ace08fbdc4e
        unzip -p archive.zip system.img > system.img
        rm archive.zip
        check system.img 10238496768 3d918b88d7ab4cf8f49854414d872f8c959d13d0a62e3f2691a5f5c9c0a57189
        ;;
    pyb110)
        gh run download 44932670737 --repo DeathThoughts/OneOS-test --name pyb110-system-img-untested --dir artifact
        if [ -f artifact/system.img ]; then
            mv artifact/system.img system.img
        elif [ -f artifact/OneOS_ColorOS17_OnePlus16.img.gz ]; then
            check artifact/OneOS_ColorOS17_OnePlus16.img.gz 7437913442 11b89e48e57a924fbd3c91f6b3839913514a50273da87cf88f86201532010569
            gzip -dc artifact/OneOS_ColorOS17_OnePlus16.img.gz > system.img
        else
            echo "Pinned PYB110 artifact has no recognized image." >&2
            exit 1
        fi
        rm -rf artifact
        check system.img 13810913280 1b7057a78c6a0f834e5020832c3ef2f82cafba5570d62559e9cb2d6761d86f4c
        ;;
    *) echo "Unsupported ColorOS image: $image" >&2; exit 2 ;;
esac
e2fsck -fn system.img
