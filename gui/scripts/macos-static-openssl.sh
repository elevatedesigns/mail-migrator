#!/bin/sh
# Rebuild the Perl modules that use OpenSSL so that OpenSSL is linked in
# statically. Without this, the packaged imapsync would need Homebrew's
# OpenSSL on every Mac it runs on.
#
# Usage: scripts/macos-static-openssl.sh [cpanm options, e.g. -L local]
set -eu

brew list openssl@3 >/dev/null 2>&1 || brew install openssl@3
SRC=$(brew --prefix openssl@3)
PREFIX=$(mktemp -d)/openssl-static
mkdir -p "$PREFIX/lib"
ln -s "$SRC/include" "$PREFIX/include"
ln -s "$SRC/bin" "$PREFIX/bin"
# Only the .a archives: the linker then has no dylib to pick.
cp "$SRC/lib/libssl.a" "$SRC/lib/libcrypto.a" "$PREFIX/lib/"
mkdir -p "$PREFIX/lib/pkgconfig"
for pc in libssl libcrypto openssl; do
    sed -e "s|^prefix=.*|prefix=$PREFIX|" "$SRC/lib/pkgconfig/$pc.pc" > "$PREFIX/lib/pkgconfig/$pc.pc"
done

export OPENSSL_PREFIX="$PREFIX"
export PKG_CONFIG_PATH="$PREFIX/lib/pkgconfig"
export PATH="$PREFIX/bin:$PATH"
cpanm -n --reinstall "$@" Net::SSLeay Crypt::OpenSSL::Random Crypt::OpenSSL::Bignum Crypt::OpenSSL::RSA

# Fail loudly if a module perl actually loads still uses a dynamic OpenSSL.
status=0
for bundle in $(perl -MNet::SSLeay -MCrypt::OpenSSL::RSA -MCrypt::OpenSSL::Bignum \
        -MCrypt::OpenSSL::Random -e 'print "$_\n" for @DynaLoader::dl_shared_objects'); do
    if otool -L "$bundle" | grep -q 'libssl\|libcrypto'; then
        echo "Still linked dynamically: $bundle" >&2
        status=1
    fi
done
exit $status
