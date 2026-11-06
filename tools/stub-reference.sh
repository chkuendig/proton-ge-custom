#!/usr/bin/env bash
set -euo pipefail
git init -q /work/origin
git -C /work/origin fetch --depth=1 https://github.com/ValveSoftware/wine.git e60892cc2398b0a83c332a2078c7b85e47bdb42b
git -C /work/origin checkout -q --detach FETCH_HEAD
mkdir -p /work/stubs
sha256sum /work/origin/dlls/nvcuda/*
for layout in old new; do
    root=/work/build
    [[ $layout != old ]] || root+=/wine
    source=$root/src-wine
    mkdir -p "$source"
    rsync -a --exclude=.git /work/origin/ "$source/"
    (cd "$source" && autoreconf -fiv)
    for arch in x86_64 i386; do
        triplet=x86_64-linux-gnu
        flags='-mcmodel=small -march=nocona -mtune=core-avx2 -mfpmath=sse'
        epoch=1791559295
        configure=(--enable-archs=x86_64 --enable-win64)
        pe=x86_64-windows
        if [[ $arch == i386 ]]; then
            triplet=i686-linux-gnu
            flags='-mstackrealign -march=nocona -mtune=core-avx2 -mfpmath=sse'
            epoch=1791559305
            configure=(--with-wine-tools="$root/obj-wine-x86_64")
            pe=i386-windows
        fi
        export CC=$triplet-gcc CXX=$triplet-g++ SOURCE_DATE_EPOCH=$epoch
        export CFLAGS="-Wno-discarded-qualifiers $flags -O2 -fwrapv -fno-strict-aliasing -ggdb -ffunction-sections -fdata-sections -fno-omit-frame-pointer -ffile-prefix-map=$source=. -Wl,--exclude-libs=libstdc++.a"
        export CPPFLAGS=$CFLAGS CROSSCFLAGS=$CFLAGS CXXFLAGS="-std=c++17 $CFLAGS"
        object=$root/obj-wine-$arch
        mkdir -p "$object"
        (cd "$object" && "$source/configure" --prefix="$root/dst-wine-$arch" --libdir="$root/dst-wine-$arch/lib" --host="$triplet" --without-all --enable-werror --with-mingw=gcc --disable-tests "${configure[@]}")
        make -C "$object" -j3 SOURCE_DATE_EPOCH=1791559305 dlls/nvcuda/$pe/nvcuda.dll
        output=/work/stubs/$layout-$arch.dll
        objcopy -p --set-section-flags .text=contents,alloc,load,readonly,code --strip-debug "$object/dlls/nvcuda/$pe/nvcuda.dll" "$output"
        sha256sum "$output"
        stat -c '%s %n' "$output"
        objdump -p "$output" | head -45 || true
    done
done
