"""Avatar composer — ported from peeps-generator.

Composes modular Open Peeps SVG parts (body, hair, face, facial_hair,
accessory) into complete character illustrations. Parts live in
`static/peeps_parts/<category>/<name>.svg` as plain SVG <g> fragments.

To add new parts: drop an SVG file into the right folder — it auto-appears.

Two endpoints expose this:
  - /api/avatar/compose  → full character (for Dialog Center bust + cards)
  - /api/avatar/thumb/<category>/<name>  → single-part thumbnail (for face picker)
  - /api/avatar/parts    → list all available parts by category
"""
from __future__ import annotations

import os
from typing import Any

from ..config import BASE_DIR

# ============================================================================
# Parts directory
# ============================================================================

PARTS_DIR = BASE_DIR / "static" / "peeps_parts"

CATEGORIES = [
    {"id": "body_bust",   "label": "Bust",       "required": True},
    {"id": "body_sit",    "label": "Sitting",    "required": True},
    {"id": "body_stand",  "label": "Standing",   "required": True},
    {"id": "hair",        "label": "Hair",       "required": True},
    {"id": "face",        "label": "Face",       "required": True},
    {"id": "facial_hair", "label": "Facial hair","required": False},
    {"id": "accessory",   "label": "Accessory",  "required": False},
]

BODY_CATS = {"body_bust", "body_sit", "body_stand"}

# Open Peeps composition transforms (from react-peeps Head component)
HEAD_TRANSFORM = "translate(225 0)"
FACE_TRANSFORM = "translate(159 186)"
FACIAL_HAIR_TRANSFORM = "translate(123 338)"
ACCESSORY_TRANSFORM = "translate(47 241)"

# Viewbox per-category for thumbnails (zoomed to show only that part)
THUMB_VIEWBOX = {
    "body_bust":   "0 380 850 820",
    "body_sit":    "0 380 850 820",
    "body_stand":  "0 380 850 820",
    "hair":        "-170 -140 930 930",
    "face":        "-40 -30 280 290",
    "facial_hair": "-240 -90 1100 410",
    "accessory":   "-40 -80 480 280",
}


# ============================================================================
# In-memory cache
# ============================================================================

_svg_cache: dict[str, str] = {}


def clear_cache() -> None:
    _svg_cache.clear()


# ============================================================================
# Part loading
# ============================================================================

def list_parts(category: str) -> list[str]:
    """List SVG filenames (without extension) in a category folder."""
    cat_dir = PARTS_DIR / category
    if not cat_dir.is_dir():
        return []
    return sorted(f.stem for f in cat_dir.iterdir() if f.suffix == ".svg")


def read_part(category: str, name: str) -> str:
    """Read the raw SVG <g> fragment for a part. Returns empty string if missing."""
    if not name:
        return ""
    path = PARTS_DIR / category / f"{name}.svg"
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def all_parts() -> dict[str, dict]:
    """Return full part catalog for UI: {category_id: {label, required, parts}}."""
    result = {}
    for cat in CATEGORIES:
        cid = cat["id"]
        result[cid] = {
            "label": cat["label"],
            "required": cat["required"],
            "parts": list_parts(cid),
        }
    return result


# ============================================================================
# Compose full character
# ============================================================================

def compose_svg(
    body_category: str = "body_bust",
    body_name: str = "Shirt",
    hair_name: str = "Medium",
    face_name: str = "Calm",
    facial_hair_name: str | None = None,
    accessory_name: str | None = None,
    bg_color: str | None = None,
    width: int | None = None,
    height: int | None = None,
    view_box: str | None = None,
    line_color: str | None = None,
) -> str:
    body_svg = read_part(body_category, body_name)
    hair_svg = read_part("hair", hair_name)
    face_svg = read_part("face", face_name)
    fh_svg = read_part("facial_hair", facial_hair_name) if facial_hair_name else ""
    acc_svg = read_part("accessory", accessory_name) if accessory_name else ""

    vb = view_box or "0 0 850 1200"
    try:
        vb_parts = vb.split()
        vb_x, vb_y, vb_w, vb_h = [float(p) for p in vb_parts]
    except Exception:
        vb_x, vb_y, vb_w, vb_h = 0, 0, 850, 1200
        vb = "0 0 850 1200"

    bg_rect = ""
    if bg_color:
        bg_rect = '<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="#{c}"/>'.format(
            x=vb_x, y=vb_y, w=vb_w, h=vb_h, c=bg_color.lstrip("#")
        )

    w = width or 850
    h = height or 1200
    svg = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vb}"'
        ' width="{w}" height="{h}">\n'
        "  {bg}\n"
        "  <g>\n"
        "    {body}\n"
        '    <g transform="{ht}">\n'
        "      <g>{hair}</g>\n"
        '      <g transform="{ft}">{face}</g>\n'
        '      <g transform="{fht}">{fh}</g>\n'
        '      <g transform="{at}">{acc}</g>\n'
        "    </g>\n"
        "  </g>\n"
        "</svg>"
    ).format(
        vb=vb,
        w=w,
        h=h,
        bg=bg_rect,
        body=body_svg,
        ht=HEAD_TRANSFORM,
        hair=hair_svg,
        ft=FACE_TRANSFORM,
        face=face_svg,
        fht=FACIAL_HAIR_TRANSFORM,
        fh=fh_svg,
        at=ACCESSORY_TRANSFORM,
        acc=acc_svg,
    )

    # Peeps SVGs use `fill=""` (empty-string fill) for the black line-art
    # paths — empty fill inherits to black at render time. To recolor only
    # the lines (not the white body fills tagged `fill="#FFFFFF"`), swap
    # the empty-fill markers to the requested colour. Both quote styles
    # appear in the source SVGs, so handle both.
    if line_color:
        svg = svg.replace('fill=""', f'fill="{line_color}"')
        svg = svg.replace("fill=''", f"fill='{line_color}'")
    return svg


def compose_from_config(cfg: dict) -> str:
    """Compose an avatar from a JSON config dict (as stored in agents.avatar_config)."""
    return compose_svg(
        body_category=cfg.get("body_type", "body_bust"),
        body_name=cfg.get("body", "Shirt"),
        hair_name=cfg.get("hair", "Medium"),
        face_name=cfg.get("face", "Calm"),
        facial_hair_name=cfg.get("facial_hair"),
        accessory_name=cfg.get("accessory"),
        bg_color=cfg.get("bg"),
        view_box=cfg.get("vb"),
        width=cfg.get("w"),
        height=cfg.get("h"),
        line_color=cfg.get("line_color"),
    )


# ============================================================================
# Thumbnails — single-part preview with appropriate viewbox
# ============================================================================

def _wrap_svg(inner: str, vb: str = "0 0 850 1200", bg: str = "#ffffff") -> str:
    bg_rect = ""
    try:
        x, y, w, h = vb.split()
    except ValueError:
        x, y, w, h = "0", "0", "850", "1200"
    if bg:
        bg_rect = (
            '<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{bg}"/>'.format(
                x=x, y=y, w=w, h=h, bg=bg
            )
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vb}">\n'
        "{bg}{inner}\n</svg>"
    ).format(vb=vb, bg=bg_rect, inner=inner)


def compose_thumb(category: str, name: str) -> str:
    """Single-part thumbnail used in the face-picker grid."""
    vb = THUMB_VIEWBOX.get(category, "0 0 850 1200")

    if category in BODY_CATS:
        return _wrap_svg(read_part(category, name), vb)
    if category == "hair":
        return _wrap_svg(read_part("hair", name), vb, "#f8f8f8")
    if category == "face":
        return _wrap_svg(read_part("face", name), vb)
    if category == "facial_hair":
        return _wrap_svg(read_part("facial_hair", name), vb)
    if category == "accessory":
        return _wrap_svg(read_part("accessory", name), vb)

    # Fallback
    return compose_svg()


# ============================================================================
# Cached wrappers
# ============================================================================

def compose_cached(cache_key: str, builder) -> str:
    if cache_key in _svg_cache:
        return _svg_cache[cache_key]
    svg = builder()
    _svg_cache[cache_key] = svg
    return svg
