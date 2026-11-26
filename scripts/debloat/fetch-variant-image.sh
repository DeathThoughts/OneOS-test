#!/usr/bin/env bash
# The same raw pins as the live Images.kt catalog; no foreign image substitution.
set -euo pipefail
id=$1
out=$2
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
mkdir -p "$out"
out=$(realpath "$out")
cd "$out"
check() {
    test "$(stat -c %s "$1")" = "$2"
    printf '%s  %s\n' "$3" "$1" | sha256sum --strict --check
}
case "$id" in
    coloros17-twinity4c) bash "$ROOT/scripts/fetch-coloros-parity-image.sh" twinity "$out" ;;
    coloros17-pyb110) bash "$ROOT/scripts/fetch-coloros-parity-image.sh" pyb110 "$out" ;;
    hyperos-popsicle)
        python3 - "$ROOT" "$out" <<'PY'
import runpy, sys
from pathlib import Path
m = runpy.run_path(sys.argv[1] + "/scripts/build-popsicle-payloads.py")
m["prepare_image"](Path(sys.argv[2]), None)
PY
        check system.img 7203971072 a0d622cff955e907b89bf7691d6d12102587bf9333ad89e92aebee0fe57b12ba
        ;;
    hyperos-madrid)
        gh run download 44753230951 --repo DeathThoughts/OneOS-test --name madrid-system --dir artifact
        mapfile -t files < <(find artifact -type f -name OneOS_HyperOS4_Madrid.zip)
        test "${#files[@]}" = 1
        check "${files[0]}" 4479942793 c4598e28013122112f7dcb2736723362cb3bff3058f5b0f8166f0acaeb3a669b
        unzip -p "${files[0]}" system.img > system.img
        rm -rf artifact
        check system.img 7818280960 ad674d33986b18bfc1d5c3d4d4878573e32b6241c2e66815deb9d4a6d86163e3
        ;;
    pixelos17-onyx)
        curl --fail --location --silent --show-error --retry 3 --connect-timeout 30 --max-time 1800 \
          'https://sourceforge.net/projects/oneos-mirror/files/images/OneOS_PixelOS17_POCOF7.img.gz/download' -o archive.gz
        check archive.gz 2556059879 4362ae40bb217d9a51934d5e48f6678d460c0152327f74db654f3c4f9930f7ca
        gzip -dc archive.gz > system.img
        rm archive.gz
        check system.img 5046312960 7913053cc74036ad5df18d188c4b47411856e4ea1b2b94389ce567f15489cdf3
        ;;
    *) exit 2 ;;
esac
