#!/usr/bin/env python3
"""Compose complete peeps SVGs by extracting the `<g>` JSX blocks from
react-peeps TSX files and wrapping them in a proper <svg> element.

The react-peeps library structures a full character as:

  <svg viewBox="0 0 850 1200">
    <g>
      <Pose body=... />                       # e.g. StandingShirt, SittingCrossedLegs
      <g transform="translate(225 0)">
        <g>                                   # hair
          <Hair />
        </g>
        <g transform="translate(159 186)">    # face
          <Face />
        </g>
      </g>
    </g>
  </svg>

Each TSX file is a single React component returning one `<g>...</g>` block.
This script fetches a body + hair + face triplet, strips React attribute
syntax and dynamic expressions, then composes a standalone .svg file.
"""
import os
import re
import sys
import urllib.request

REPO = "https://raw.githubusercontent.com/CeamKrier/react-peeps/master"
OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "peeps_svg")
os.makedirs(OUT_DIR, exist_ok=True)

CACHE: dict = {}


def fetch_tsx(path: str) -> str:
    if path in CACHE:
        return CACHE[path]
    with urllib.request.urlopen(REPO + "/" + path) as r:
        src = r.read().decode("utf-8")
    CACHE[path] = src
    return src


def extract_g_block(tsx_src: str) -> str:
    """Pull the outermost <g ...> ... </g> from a react component source."""
    start = tsx_src.find("<g")
    if start == -1:
        raise ValueError("no <g> in TSX")
    depth = 0
    i = start
    while i < len(tsx_src):
        if tsx_src.startswith("<g", i):
            j = tsx_src.find(">", i)
            # skip self-closing
            if j != -1 and tsx_src[j - 1] == "/":
                i = j + 1
                continue
            depth += 1
            i = j + 1
            continue
        if tsx_src.startswith("</g>", i):
            depth -= 1
            i += 4
            if depth == 0:
                return tsx_src[start:i]
            continue
        i += 1
    raise ValueError("unbalanced <g>")


def clean_jsx(block: str, stroke: str = "#000000", bg: str = "#ffffff") -> str:
    """Strip JSX expressions and convert attribute names to XML conventions."""
    # fill={backgroundColor || '#FFFFFF'} → fill="#FFFFFF"
    block = re.sub(
        r"fill=\{[^{}]*?backgroundColor[^{}]*?\|\|\s*'([^']+)'[^{}]*?\}",
        lambda m: f'fill="{m.group(1)}"', block,
    )
    block = re.sub(
        r"fill=\{[^{}]*?backgroundColor[^{}]*?\}",
        f'fill="{bg}"', block,
    )
    block = re.sub(
        r"stroke=\{[^{}]*?strokeColor[^{}]*?\|\|\s*'([^']+)'[^{}]*?\}",
        lambda m: f'stroke="{m.group(1)}"', block,
    )
    block = re.sub(
        r"stroke=\{[^{}]*?strokeColor[^{}]*?\}",
        f'stroke="{stroke}"', block,
    )
    # any remaining {} expressions → empty
    block = re.sub(r"\{[^{}]*\}", '""', block)
    # strokeWidth → stroke-width, fillRule → fill-rule, strokeMiterlimit, etc.
    block = re.sub(r"strokeWidth", "stroke-width", block)
    block = re.sub(r"strokeLinejoin", "stroke-linejoin", block)
    block = re.sub(r"strokeLinecap", "stroke-linecap", block)
    block = re.sub(r"strokeMiterlimit", "stroke-miterlimit", block)
    block = re.sub(r"strokeOpacity", "stroke-opacity", block)
    block = re.sub(r"fillRule", "fill-rule", block)
    block = re.sub(r"fillOpacity", "fill-opacity", block)
    block = re.sub(r"clipRule", "clip-rule", block)
    block = re.sub(r"clipPath", "clip-path", block)
    return block


def component_block(path: str, stroke: str = "#000000", bg: str = "#ffffff") -> str:
    src = fetch_tsx(path)
    g = extract_g_block(src)
    return clean_jsx(g, stroke, bg)


def compose(
    slug: str,
    pose_path: str,
    hair_path: str,
    face_path: str,
    stroke: str = "#000000",
    bg: str = "#ffffff",
) -> str:
    """Compose a full peep SVG and write to OUT_DIR/slug.svg."""
    pose_g = component_block(pose_path, stroke, bg)
    hair_g = component_block(hair_path, stroke, bg)
    face_g = component_block(face_path, stroke, bg)

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 850 1200">
  <g>
    {pose_g}
    <g transform="translate(225 0)">
      <g>{hair_g}</g>
      <g transform="translate(159 186)">{face_g}</g>
    </g>
  </g>
</svg>
'''
    out = os.path.join(OUT_DIR, f"{slug}.svg")
    with open(out, "w", encoding="utf-8") as f:
        f.write(svg)
    size = os.path.getsize(out)
    print(f"  {slug}.svg  ({size} bytes)")
    return out


def main():
    print("composing peep SVGs →", OUT_DIR)

    # Standing poses
    compose("standing_shirt",          "src/peeps/pose/standing/ShirtBW.tsx",        "src/peeps/hair/Medium.tsx",     "src/peeps/face/Calm.tsx")
    compose("standing_pointing",       "src/peeps/pose/standing/PointingFingerBW.tsx","src/peeps/hair/MediumShort.tsx","src/peeps/face/Driven.tsx")
    compose("standing_walking",        "src/peeps/pose/standing/WalkingBW.tsx",      "src/peeps/hair/Pomp.tsx",       "src/peeps/face/Smile.tsx")
    compose("standing_blazer_pants",   "src/peeps/pose/standing/BlazerPantsBW.tsx",  "src/peeps/hair/Bun.tsx",        "src/peeps/face/Cheeky.tsx")

    # Sitting poses
    compose("sitting_crossed_legs",    "src/peeps/pose/sitting/CrossedLegs.tsx",     "src/peeps/hair/LongCurly.tsx",  "src/peeps/face/Calm.tsx")
    compose("sitting_one_leg_up",      "src/peeps/pose/sitting/OneLegUpBW.tsx",      "src/peeps/hair/Afro.tsx",       "src/peeps/face/Smile.tsx")
    compose("sitting_medium",          "src/peeps/pose/sitting/MediumBW.tsx",        "src/peeps/hair/Medium.tsx",     "src/peeps/face/Driven.tsx")
    compose("sitting_wheelchair",      "src/peeps/pose/sitting/Wheelchair.tsx",      "src/peeps/hair/MediumShort.tsx","src/peeps/face/Cheeky.tsx")

    # Bust poses (half body with hands)
    compose("bust_coffee",             "src/peeps/pose/bust/Coffee.tsx",             "src/peeps/hair/Medium.tsx",     "src/peeps/face/Calm.tsx")
    compose("bust_explaining",         "src/peeps/pose/bust/Explaining.tsx",         "src/peeps/hair/Pomp.tsx",       "src/peeps/face/Driven.tsx")
    compose("bust_gaming",             "src/peeps/pose/bust/Gaming.tsx",             "src/peeps/hair/MediumShort.tsx","src/peeps/face/Cheeky.tsx")
    compose("bust_hoodie",             "src/peeps/pose/bust/Hoodie.tsx",             "src/peeps/hair/Bun.tsx",        "src/peeps/face/Smile.tsx")
    compose("bust_device",             "src/peeps/pose/bust/Device.tsx",             "src/peeps/hair/LongCurly.tsx",  "src/peeps/face/Calm.tsx")
    compose("bust_pointing_up",        "src/peeps/pose/bust/PointingUp.tsx",         "src/peeps/hair/Afro.tsx",       "src/peeps/face/Driven.tsx")

    # Animation frames — same character, different body.
    # Head/face/hair are locked so the character identity is stable across frames.
    SAME_HAIR = "src/peeps/hair/MediumShort.tsx"
    SAME_FACE = "src/peeps/face/Calm.tsx"
    compose("anim_01_gaming",    "src/peeps/pose/bust/Gaming.tsx",    SAME_HAIR, SAME_FACE)
    compose("anim_02_coffee",    "src/peeps/pose/bust/Coffee.tsx",    SAME_HAIR, SAME_FACE)
    compose("anim_03_device",    "src/peeps/pose/bust/Device.tsx",    SAME_HAIR, SAME_FACE)
    compose("anim_04_pointing",  "src/peeps/pose/bust/PointingUp.tsx",SAME_HAIR, SAME_FACE)


if __name__ == "__main__":
    main()
