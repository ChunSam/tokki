#!/usr/bin/env bash
# Builds dist/tokki_<version>_all.deb from linux/. Needs only dpkg-deb.
#   linux/packaging/build_deb.sh                  # version from linux/tokki/__init__.py
#   TOKKI_VERSION=1.4.0 linux/packaging/build_deb.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEFAULT_VERSION="$(sed -n 's/^VERSION = "\(.*\)"$/\1/p' "$ROOT/linux/tokki/__init__.py")"
VERSION="${TOKKI_VERSION:-$DEFAULT_VERSION}"
OUT_DIR="${OUT_DIR:-$ROOT/dist}"
if [ -z "$VERSION" ]; then
  echo "could not read the version from linux/tokki/__init__.py" >&2
  exit 1
fi

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
PKG="$STAGE/tokki"

install -d "$PKG/DEBIAN" "$PKG/usr/bin" "$PKG/usr/lib/tokki" "$PKG/usr/share/applications" \
  "$PKG/usr/share/pixmaps" "$PKG/usr/share/doc/tokki" "$PKG/usr/share/tokki"

# The package, without bytecode caches from a local test run.
cp -r "$ROOT/linux/tokki" "$PKG/usr/lib/tokki/"
find "$PKG/usr/lib/tokki" -name '__pycache__' -type d -prune -exec rm -rf {} +

# A release stamps its own version into the installed copy, so --version and
# the menu report what was shipped.
sed -i "s/^VERSION = \".*\"$/VERSION = \"$VERSION\"/" "$PKG/usr/lib/tokki/tokki/__init__.py"

cat > "$PKG/usr/bin/tokki" <<'EOF'
#!/usr/bin/python3
import runpy
import sys

sys.path.insert(0, "/usr/lib/tokki")
runpy.run_module("tokki", run_name="__main__", alter_sys=True)
EOF

install -m 0755 "$ROOT/scripts/claude-statusline-bridge.sh" "$PKG/usr/share/tokki/claude-statusline-bridge.sh"
install -m 0644 "$ROOT/linux/packaging/tokki.desktop" "$PKG/usr/share/applications/tokki.desktop"
install -m 0644 "$ROOT/Sources/TokenTrackerMenuBar/Resources/AppIcon.png" "$PKG/usr/share/pixmaps/tokki.png"
install -m 0644 "$ROOT/LICENSE" "$PKG/usr/share/doc/tokki/copyright"

cat > "$PKG/DEBIAN/control" <<EOF
Package: tokki
Version: $VERSION
Section: utils
Priority: optional
Architecture: all
Depends: python3 (>= 3.10), python3-gi, gir1.2-gtk-3.0, gir1.2-ayatanaappindicator3-0.1 | gir1.2-appindicator3-0.1, xdg-utils
Recommends: gir1.2-notify-0.7, gnome-shell-extension-appindicator
Installed-Size: $(du -sk "$PKG/usr" | cut -f1)
Maintainer: ChunSam <https://github.com/ChunSam/tokki>
Homepage: https://github.com/ChunSam/tokki
Description: Claude Code and Codex usage limits in the system tray
 Unofficial tray app that shows the remaining 5-hour and weekly usage of
 Claude Code and Codex. It only reads the tokens the CLIs store.
EOF

chmod 0755 "$PKG" "$PKG/DEBIAN"
chmod -R u=rwX,go=rX "$PKG/usr"
chmod 0755 "$PKG/usr/bin/tokki" "$PKG/usr/share/tokki/claude-statusline-bridge.sh"

mkdir -p "$OUT_DIR"
DEB="$OUT_DIR/tokki_${VERSION}_all.deb"
dpkg-deb --root-owner-group --build "$PKG" "$DEB" >/dev/null
echo "$DEB"
