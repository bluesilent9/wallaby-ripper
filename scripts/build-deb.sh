#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
output=${1:-"$root/.."}
mkdir -p "$output"
output=$(cd "$output" && pwd)
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT HUP INT TERM
mkdir -p "$stage/DEBIAN" "$stage/usr/share/wallaby-ripper" "$stage/usr/bin" \
 "$stage/usr/share/applications" "$stage/usr/share/icons/hicolor/scalable/apps" \
 "$stage/usr/share/doc/wallaby-ripper"
cp -R "$root/wallaby_ripper" "$root/scripts" "$root/vendor" "$stage/usr/share/wallaby-ripper/"
find "$stage" -type d -name __pycache__ -exec rm -rf {} +
install -m755 "$root/packaging/wallaby-ripper" "$stage/usr/bin/wallaby-ripper"
install -m644 "$root/packaging/wallaby-ripper.desktop" "$stage/usr/share/applications/"
for size in 16 24 32 48 64 128 256 512; do
 mkdir -p "$stage/usr/share/icons/hicolor/${size}x${size}/apps"
 install -m644 "$root/packaging/icons/$size/wallaby-ripper.png" "$stage/usr/share/icons/hicolor/${size}x${size}/apps/"
done
cp "$root/README.md" "$root/LICENSE" "$root/THIRD-PARTY.txt" "$root/VALIDATION.md" "$stage/usr/share/doc/wallaby-ripper/"
cat > "$stage/DEBIAN/control" <<CONTROL
Package: wallaby-ripper
Version: 1.0.0
Section: utils
Priority: optional
Architecture: all
Maintainer: Wallaby Ripper maintainers <maintainers@wallaby-ripper.invalid>
Depends: python3 (>= 3.4), build-essential, libssl-dev, libcurl4-openssl-dev | libcurl4-gnutls-dev, libzip-dev, zlib1g-dev, poppler-utils, xdg-utils, ca-certificates
Installed-Size: $(du -sk "$stage/usr" | cut -f1)
Description: Local browser-based ACSM to PDF converter
 Automatically obtains an ADEPT authorization, downloads the PDF associated
 with an ACSM ticket, decrypts it with libgourou, and provides a browser download.
 Bundled processing tools compile locally on first launch for compatibility
 across maintained Ubuntu and Kubuntu releases, including Ubuntu Pro/Legacy.
CONTROL
find "$stage" -type d -exec chmod 755 {} +
chmod -R go-w "$stage"
# gzip instead of zstd so even older supported dpkg versions can install it.
dpkg-deb -Zgzip --root-owner-group --build "$stage" "$output/wallaby-ripper_1.0.0_all.deb"
