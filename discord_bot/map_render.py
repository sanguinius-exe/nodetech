"""Renders a World's grid as a PNG for Discord (main.py's own `map` command writes an
interactive HTML page and opens a browser tab, which has nowhere to go in a chat message).
Colors come straight from main.py's own assign_country_colors()/MAP_TILE_COLORS/
MAP_UNCLAIMED_COLOR, so a country's color here always matches what the CLI's `map` command and
the web terminal already show it as - one palette, not a second one to keep in sync.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw  # noqa: E402

import main as game  # noqa: E402
from main import World  # noqa: E402

PADDING = 12
LEGEND_ROW_HEIGHT = 20
LEGEND_SWATCH_SIZE = 14
BACKGROUND = (28, 28, 31)
TEXT_COLOR = (230, 230, 230)


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    hex_color = hex_color.lstrip("#")
    return (int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16))


def render_map(world: World) -> io.BytesIO:
    """A PNG of the world's grid, one filled square per node (colored by owning country, gray
    for unclaimed, darker for empty grid slots with no node at all), plus a color-keyed legend
    of every country underneath. Returns a ready-to-send BytesIO, already seeked to the start."""
    # Same tile-size clamp build_map_html() uses, so a huge grid still produces a reasonably
    # sized image instead of one too large for Discord's upload limit.
    tile_size = max(4, min(32, 2000 // max(world.width, world.height, 1)))
    country_colors = {name: _hex_to_rgb(color) for name, color in game.assign_country_colors(world).items()}
    unclaimed_color = _hex_to_rgb(game.MAP_UNCLAIMED_COLOR)
    empty_color = _hex_to_rgb(game.MAP_EMPTY_COLOR)
    grid = {(n.x, n.y): n for n in world.nodes.values()}

    map_width = world.width * tile_size
    map_height = world.height * tile_size
    legend_height = LEGEND_ROW_HEIGHT * len(world.countries) if world.countries else 0
    image = Image.new(
        "RGB",
        (map_width + PADDING * 2, map_height + legend_height + PADDING * (3 if legend_height else 2)),
        BACKGROUND,
    )
    draw = ImageDraw.Draw(image)

    for y in range(world.height):
        for x in range(world.width):
            node = grid.get((x, y))
            if node is None:
                color = empty_color
            elif node.country:
                color = country_colors.get(node.country, unclaimed_color)
            else:
                color = unclaimed_color
            top_left = (PADDING + x * tile_size, PADDING + y * tile_size)
            bottom_right = (top_left[0] + tile_size - 1, top_left[1] + tile_size - 1)
            draw.rectangle([top_left, bottom_right], fill=color)

    legend_y = PADDING + map_height + PADDING
    for i, name in enumerate(world.countries):
        color = country_colors[name]
        swatch_top = legend_y + i * LEGEND_ROW_HEIGHT
        draw.rectangle(
            [PADDING, swatch_top, PADDING + LEGEND_SWATCH_SIZE, swatch_top + LEGEND_SWATCH_SIZE], fill=color
        )
        draw.text((PADDING + LEGEND_SWATCH_SIZE + 6, swatch_top), name, fill=TEXT_COLOR)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer
