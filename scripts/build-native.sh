#!/bin/sh
# Build only bundled, pinned sources; no network and no root privileges.
set -eu
src=$1
out=$2
mkdir -p "$out"
src=$(cd "$src" && pwd)
out=$(cd "$out" && pwd)
# Build from a writable copy, never modify package-owned files.
build="$out/build"
rm -rf "$build"
mkdir -p "$build"
cp -R "$src/." "$build/"
cd "$build"
extra="-I$build/lib/pugixml/src ${WALLABY_BUILD_CPPFLAGS:-}"
libs=${WALLABY_BUILD_LDFLAGS:-}
g++ -O2 -std=c++11 -fPIC -Ilib/pugixml/src -c lib/pugixml/src/pugixml.cpp -o pugixml.o
ar rcs libpugixml.a pugixml.o
make -C lib/updfparser BUILD_STATIC=1 BUILD_SHARED=0 CXXFLAGS="-O2 -std=c++11 -fPIC -I./include $extra"
make BUILD_STATIC=1 BUILD_SHARED=0 BUILD_UTILS=0 CPPFLAGS="$extra" CXXFLAGS="-O2 -std=c++11 -fPIC -I./include -I./lib/updfparser/include"
make -C utils ROOT="$build" STATIC_UTILS=1 CPPFLAGS="$extra" \
 CXXFLAGS="-O2 -std=c++11 -fPIC -I$build/include" \
 LDFLAGS="$build/lib/updfparser/libupdfparser.a $libs -lcrypto -lzip -lz -lcurl $build/libpugixml.a" \
 acsmdownloader adept_remove adept_activate
for tool in acsmdownloader adept_remove adept_activate; do
 cp "utils/$tool" "$out/$tool"
done
rm -rf "$build"
