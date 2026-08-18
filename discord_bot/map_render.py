"""Renders a World's grid as a PNG for Discord (main.py's own `map` command writes an
interactive HTML page and opens a browser tab, which has nowhere to go in a chat message).
Colors come straight from main.py's own assign_country_colors()/MAP_TILE_COLORS/
MAP_UNCLAIMED_COLOR/MAP_EMPTY_COLOR, so a country's color here always matches what the CLI's
`map` command and the web terminal already show it as - one palette, not a second one to keep in
sync. The overall look (dark page background, 1px gaps between tiles, a year badge in the
top-right corner, a wrapped legend of swatches + names) mirrors main.py's MAP_CSS_TEMPLATE for
the same reason - one visual language across every frontend, not a bespoke one just for Discord.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

import main as game  # noqa: E402
from main import World  # noqa: E402

PAGE_PADDING = 24
TILE_GAP = 1
BACKGROUND = (28, 28, 31)  # #1c1c1f - main.py's .map-page background
TEXT_COLOR = (238, 238, 238)  # #eee
BADGE_BG = (38, 38, 42)  # #26262a - main.py's .year-badge background

TITLE_FONT_SIZE = 20
BADGE_FONT_SIZE = 14
LEGEND_FONT_SIZE = 13
AXIS_FONT_SIZE = 11
AXIS_COLOR = (140, 140, 145)

HEADER_HEIGHT = 40
BADGE_PADDING_X = 16
BADGE_PADDING_Y = 10

AXIS_LABEL_PADDING = 6  # gap between an axis label and the grid edge it's next to
# "Nice" intervals to space coordinate labels at - the smallest one that still keeps labels at
# least AXIS_MIN_LABEL_SPACING_PX apart along that axis wins, so a huge world doesn't try to
# print every single column/row number on top of itself.
AXIS_LABEL_STEPS = [1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000]
AXIS_MIN_LABEL_SPACING_PX = 34

LEGEND_TOP_MARGIN = 20
LEGEND_ROW_HEIGHT = 22
LEGEND_SWATCH_SIZE = 12
LEGEND_ITEM_GAP = 20

# How many tiles of surrounding context to include on each side of a country's own territory
# when cropping to it, so the region reads as "part of a map" rather than floating tiles with no
# landmarks - clamped to the grid's actual edges, same as the crop itself.
CROP_PADDING_TILES = 2


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    hex_color = hex_color.lstrip("#")
    return (int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16))


def _country_bounds(world: World, country_name: str) -> tuple[int, int, int, int]:
    """(min_x, min_y, max_x, max_y), inclusive, covering `country_name`'s own nodes padded by
    CROP_PADDING_TILES and clamped to the grid. Raises ValueError if the country doesn't exist
    or owns no nodes - there's nothing sensible to crop to either way."""
    country = world.countries.get(country_name)
    if country is None:
        raise ValueError(f"No such country '{country_name}'.")
    xs = [node.x for node_id in country.nodes if (node := world.nodes.get(node_id)) is not None]
    ys = [node.y for node_id in country.nodes if (node := world.nodes.get(node_id)) is not None]
    if not xs:
        raise ValueError(f"'{country_name}' has no nodes.")
    return (
        max(0, min(xs) - CROP_PADDING_TILES),
        max(0, min(ys) - CROP_PADDING_TILES),
        min(world.width - 1, max(xs) + CROP_PADDING_TILES),
        min(world.height - 1, max(ys) + CROP_PADDING_TILES),
    )


def _axis_label_step(span: int, tile_stride: float) -> int:
    """The smallest "nice" interval (see AXIS_LABEL_STEPS) that keeps consecutive coordinate
    labels at least AXIS_MIN_LABEL_SPACING_PX apart at this tile size - so a small crop labels
    every tile, while a full 300+-tile world falls back to every 10th/50th/100th one instead of
    printing an unreadable wall of overlapping numbers."""
    for step in AXIS_LABEL_STEPS:
        if step * tile_stride >= AXIS_MIN_LABEL_SPACING_PX or step >= span:
            return step
    return AXIS_LABEL_STEPS[-1]


def _axis_label_positions(min_v: int, max_v: int, step: int) -> list[int]:
    """Coordinate values to label: min_v, then every step from there, always ending on max_v
    (added separately if the regular stride doesn't already land on it) so the map's actual
    bounds are always readable even when they fall mid-interval."""
    positions = list(range(min_v, max_v + 1, step))
    if positions[-1] != max_v:
        positions.append(max_v)
    return positions


def _wrap_legend(names: list[str], font: ImageFont.FreeTypeFont, max_width: float) -> list[list[str]]:
    """Greedy left-to-right wrapping of legend items into rows that fit max_width - the PNG
    equivalent of the web map's `flex-wrap` legend."""
    if not names:
        return []
    scratch = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    rows: list[list[str]] = []
    current: list[str] = []
    current_width = 0.0
    for name in names:
        item_width = LEGEND_SWATCH_SIZE + 6 + scratch.textlength(name, font=font)
        extra = item_width if not current else LEGEND_ITEM_GAP + item_width
        if current and current_width + extra > max_width:
            rows.append(current)
            current, current_width = [name], item_width
        else:
            current.append(name)
            current_width += extra
    rows.append(current)
    return rows


def render_map(world: World, title: Optional[str] = None, country: Optional[str] = None) -> io.BytesIO:
    """A PNG of the world's grid (or, if `country` is given, just that country's own territory
    plus a couple tiles of surrounding context - see _country_bounds): one filled tile per node
    (colored by owning country, gray for unclaimed, a slightly darker shade for empty grid slots
    with no node at all), a "Year N" badge, and a wrapped legend of every country visible in the
    rendered region. `title` (e.g. the Discord server's name) renders top-left the way main.py's
    map page shows the save name. Raises ValueError (via _country_bounds) if `country` doesn't
    exist or owns no nodes. Returns a ready-to-send BytesIO, already seeked to the start."""
    if country is not None:
        min_x, min_y, max_x, max_y = _country_bounds(world, country)
        title = f"{title} - {country}" if title else country
    else:
        min_x, min_y, max_x, max_y = 0, 0, world.width - 1, world.height - 1
    region_width = max_x - min_x + 1
    region_height = max_y - min_y + 1

    tile_size = max(4, min(32, 2000 // max(region_width, region_height, 1)))
    country_colors = {name: _hex_to_rgb(color) for name, color in game.assign_country_colors(world).items()}
    unclaimed_color = _hex_to_rgb(game.MAP_UNCLAIMED_COLOR)
    empty_color = _hex_to_rgb(game.MAP_EMPTY_COLOR)
    grid = {(n.x, n.y): n for n in world.nodes.values()}

    title_font = ImageFont.load_default(size=TITLE_FONT_SIZE)
    badge_font = ImageFont.load_default(size=BADGE_FONT_SIZE)
    legend_font = ImageFont.load_default(size=LEGEND_FONT_SIZE)
    axis_font = ImageFont.load_default(size=AXIS_FONT_SIZE)

    map_width = region_width * tile_size + (region_width - 1) * TILE_GAP
    map_height = region_height * tile_size + (region_height - 1) * TILE_GAP

    # Coordinate axis labels: x along the top, y along the left, spaced out at whatever "nice"
    # interval keeps them legible at this tile size (see _axis_label_step) - a small crop labels
    # every tile, a full huge world falls back to every 10th/50th/100th one instead.
    tile_stride = tile_size + TILE_GAP
    x_labels = _axis_label_positions(min_x, max_x, _axis_label_step(region_width, tile_stride))
    y_labels = _axis_label_positions(min_y, max_y, _axis_label_step(region_height, tile_stride))

    # Measured with a scratch context, before the real canvas exists, since a cropped region
    # (e.g. one small country's territory) can be narrower than title + badge need side by side -
    # in which case the canvas widens to fit the header instead of letting them overlap.
    scratch = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    y_axis_width = round(max(scratch.textlength(str(v), font=axis_font) for v in y_labels) + AXIS_LABEL_PADDING * 2)
    x_axis_height = AXIS_FONT_SIZE + AXIS_LABEL_PADDING * 2

    content_width = y_axis_width + map_width
    content_height = x_axis_height + map_height

    visible_names = {
        node.country
        for (x, y), node in grid.items()
        if min_x <= x <= max_x and min_y <= y <= max_y and node.country
    }
    legend_names = [name for name in world.countries if name in visible_names]
    legend_rows = _wrap_legend(legend_names, legend_font, map_width)
    legend_height = LEGEND_TOP_MARGIN + len(legend_rows) * LEGEND_ROW_HEIGHT if legend_rows else 0

    year_text = f"Year {world.year}"
    badge_width = scratch.textlength(year_text, font=badge_font) + BADGE_PADDING_X * 2
    badge_height = BADGE_FONT_SIZE + BADGE_PADDING_Y
    title_width = scratch.textlength(title, font=title_font) if title else 0
    header_width = title_width + (30 if title else 0) + badge_width

    canvas_width = round(max(content_width, header_width) + PAGE_PADDING * 2)
    canvas_height = PAGE_PADDING + HEADER_HEIGHT + content_height + legend_height + PAGE_PADDING

    image = Image.new("RGB", (canvas_width, canvas_height), BACKGROUND)
    draw = ImageDraw.Draw(image)

    if title:
        draw.text((PAGE_PADDING, PAGE_PADDING), title, font=title_font, fill=TEXT_COLOR)

    badge_left = canvas_width - PAGE_PADDING - badge_width
    draw.rounded_rectangle(
        [badge_left, PAGE_PADDING, badge_left + badge_width, PAGE_PADDING + badge_height], radius=8, fill=BADGE_BG
    )
    draw.text(
        (badge_left + BADGE_PADDING_X, PAGE_PADDING + (badge_height - BADGE_FONT_SIZE) / 2 - 1),
        year_text,
        font=badge_font,
        fill=TEXT_COLOR,
    )

    # Centered rather than pinned to PAGE_PADDING, since the canvas may have been widened past
    # content_width just to fit the header (title + badge) - a small cropped region shouldn't end
    # up shoved into the top-left corner with a wall of empty space beside it. content_left is the
    # axis labels' left edge; the tile grid itself starts y_axis_width further right.
    content_left = (canvas_width - content_width) / 2
    grid_left = content_left + y_axis_width
    grid_top = PAGE_PADDING + HEADER_HEIGHT + x_axis_height

    def tile_center(x: int, y: int) -> tuple[float, float]:
        return (
            grid_left + (x - min_x) * tile_stride + tile_size / 2,
            grid_top + (y - min_y) * tile_stride + tile_size / 2,
        )

    for label_x in x_labels:
        cx, _ = tile_center(label_x, min_y)
        text = str(label_x)
        draw.text(
            (cx - scratch.textlength(text, font=axis_font) / 2, grid_top - x_axis_height + AXIS_LABEL_PADDING / 2),
            text,
            font=axis_font,
            fill=AXIS_COLOR,
        )
    for label_y in y_labels:
        _, cy = tile_center(min_x, label_y)
        text = str(label_y)
        draw.text(
            (grid_left - y_axis_width + AXIS_LABEL_PADDING, cy - AXIS_FONT_SIZE / 2),
            text,
            font=axis_font,
            fill=AXIS_COLOR,
        )

    for y in range(min_y, max_y + 1):
        for x in range(min_x, max_x + 1):
            node = grid.get((x, y))
            if node is None:
                color = empty_color
            elif node.country:
                color = country_colors.get(node.country, unclaimed_color)
            else:
                color = unclaimed_color
            left = grid_left + (x - min_x) * tile_stride
            top = grid_top + (y - min_y) * tile_stride
            draw.rectangle([left, top, left + tile_size - 1, top + tile_size - 1], fill=color)

    legend_y = grid_top + map_height + LEGEND_TOP_MARGIN
    for row in legend_rows:
        x = grid_left
        for name in row:
            draw.rounded_rectangle(
                [x, legend_y + 4, x + LEGEND_SWATCH_SIZE, legend_y + 4 + LEGEND_SWATCH_SIZE],
                radius=2,
                fill=country_colors[name],
            )
            draw.text((x + LEGEND_SWATCH_SIZE + 6, legend_y), name, font=legend_font, fill=TEXT_COLOR)
            x += LEGEND_SWATCH_SIZE + 6 + draw.textlength(name, font=legend_font) + LEGEND_ITEM_GAP
        legend_y += LEGEND_ROW_HEIGHT

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer
