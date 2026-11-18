"""Draws the panel label with provider logos, for the logo label style.

An AppIndicator carries one icon and a line of text, and text cannot hold a
logo. So the whole label is drawn into one wide PNG and set as the icon, with an
empty text label. GNOME's AppIndicator extension draws an icon at least 1.5
times as wide as it is tall at its own size, and only when the file sits in a
user-writable place (it reads it with no caching there), so the file goes in the
user's runtime directory. Each drawing gets a new name: the extension does not
reload a path it already shows.

The panel is dark on Ubuntu whatever the theme, so the label is drawn light.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
import cairo  # noqa: E402
from gi.repository import Gdk, GdkPixbuf, GLib, Pango, PangoCairo  # noqa: E402

from .core.display import TraySegment  # noqa: E402
from .core.models import Provider  # noqa: E402

RESOURCES = Path(__file__).resolve().parent / "resources"
HEIGHT = 22
LOGO = 14
LOGO_GAP = 4
FOREGROUND = (1.0, 1.0, 1.0)
# The panel's own red reads poorly at this size; a lighter one keeps contrast.
WARNING = (1.0, 0.45, 0.42)
_LOGOS = {Provider.CLAUDE: "claude-logo.png", Provider.CODEX: "codex-logo.png"}


def output_dir() -> Path:
    return Path(GLib.get_user_runtime_dir()) / "tokki"


def _font() -> Pango.FontDescription:
    name = "Sans 11"
    try:
        from gi.repository import Gio
        name = Gio.Settings.new("org.gnome.desktop.interface").get_string("font-name") or name
    except Exception:
        pass
    return Pango.FontDescription.from_string(name)


def _logo(provider: Provider) -> GdkPixbuf.Pixbuf:
    """The logo with its transparent margin cut away, fitted into LOGO pixels, as
    StatusItemRenderer does with visibleContentRect: the two files carry
    different margins, and uncut they draw at different sizes."""
    source = GdkPixbuf.Pixbuf.new_from_file(str(RESOURCES / _LOGOS[provider]))
    x, y, width, height = _visible_rect(source)
    visible = source.new_subpixbuf(x, y, width, height)
    scale = LOGO / max(width, height)
    return visible.scale_simple(max(1, round(width * scale)), max(1, round(height * scale)), GdkPixbuf.InterpType.HYPER)


def _visible_rect(pixbuf: GdkPixbuf.Pixbuf) -> tuple[int, int, int, int]:
    width, height = pixbuf.get_width(), pixbuf.get_height()
    if not pixbuf.get_has_alpha():
        return 0, 0, width, height
    pixels, stride, channels = pixbuf.get_pixels(), pixbuf.get_rowstride(), pixbuf.get_n_channels()
    seen = [(x, y) for y in range(height) for x in range(width) if pixels[y * stride + x * channels + 3] > 8]
    if not seen:
        return 0, 0, width, height
    xs, ys = [x for x, _ in seen], [y for _, y in seen]
    return min(xs), min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1


def render(segments: list[TraySegment]) -> bytes:
    """The label as PNG bytes, HEIGHT pixels tall and as wide as it needs."""
    font = _font()
    probe = cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1))

    def layout_for(context, text):
        layout = PangoCairo.create_layout(context)
        # Subpixel smoothing assumes an opaque background behind the glyphs; on a
        # transparent image it leaves coloured fringes.
        options = cairo.FontOptions()
        options.set_antialias(cairo.ANTIALIAS_GRAY)
        options.set_hint_style(cairo.HINT_STYLE_SLIGHT)
        PangoCairo.context_set_font_options(layout.get_context(), options)
        layout.context_changed()
        layout.set_font_description(font)
        layout.set_text(text, -1)
        return layout

    pieces = []
    for piece in segments:
        if piece.provider is not None:
            pieces.append(("logo", piece, LOGO + LOGO_GAP))
        else:
            text = " · " if piece.separator else piece.text
            width, _ = layout_for(probe, text).get_pixel_size()
            pieces.append(("text", piece, width))
    # The extension draws anything narrower than 1.5x its height as a square icon.
    width = max(sum(w for _, _, w in pieces), int(HEIGHT * 1.5) + 1)

    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, HEIGHT)
    context = cairo.Context(surface)
    x = 0
    for kind, piece, piece_width in pieces:
        if kind == "logo":
            # The logos are templates: only their alpha counts, painted in the label colour.
            logo = _logo(piece.provider)
            left = x + (LOGO - logo.get_width()) // 2
            Gdk.cairo_set_source_pixbuf(context, logo, left, (HEIGHT - logo.get_height()) // 2)
            mask = context.get_source()
            context.set_source_rgb(*FOREGROUND)
            context.mask(mask)
        else:
            text = " · " if piece.separator else piece.text
            layout = layout_for(context, text)
            _, height = layout.get_pixel_size()
            context.set_source_rgb(*(WARNING if piece.warning else FOREGROUND))
            context.move_to(x, (HEIGHT - height) / 2)
            PangoCairo.show_layout(context, layout)
        x += piece_width
    surface.flush()

    chunks: list[bytes] = []
    surface.write_to_png(_Sink(chunks))
    return b"".join(chunks)


class _Sink:
    def __init__(self, chunks: list[bytes]):
        self.chunks = chunks

    def write(self, data: bytes) -> int:
        self.chunks.append(bytes(data))
        return len(data)


def write(segments: list[TraySegment], previous: Path | None) -> Path:
    """Writes the drawing under a name taken from its content and removes the
    previous one. The same label keeps the same path, so no reload is asked."""
    data = render(segments)
    folder = output_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"tray-{hashlib.sha1(data).hexdigest()[:12]}.png"
    if not path.exists():
        temp = path.with_suffix(".tmp")
        temp.write_bytes(data)
        os.replace(temp, path)
    if previous is not None and previous != path:
        try:
            previous.unlink()
        except OSError:
            pass
    return path


def clear(previous: Path | None) -> None:
    if previous is None:
        return
    try:
        previous.unlink()
    except OSError:
        pass


def clear_all() -> None:
    """Drawings left by an earlier run; one instance runs at a time."""
    for leftover in output_dir().glob("tray-*.png"):
        clear(leftover)
