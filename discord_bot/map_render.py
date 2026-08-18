"""Renders a World's grid as a PNG for Discord (main.py's own `map` command writes an
interactive HTML page and opens a browser tab, which has nowhere to go in a chat message).

render_map() colors come straight from main.py's own assign_country_colors()/MAP_TILE_COLORS/
MAP_UNCLAIMED_COLOR/MAP_EMPTY_COLOR, so a country's color here always matches what the CLI's
`map` command and the web terminal already show it as - one palette, not a second one to keep in
sync. render_heatmap() instead colors one country's own tiles by population or GDP along its own
gradient (HEATMAP_STOPS), and render_terrain() colors them by Terrain along a fixed categorical
palette (TERRAIN_COLORS) - neither has an equivalent in the CLI/web terminal, since a static image
is what makes them useful here (no hover to check exact numbers or terrain tile-by-tile). All
three share the same page chrome (dark background, 1px gaps between tiles, a year badge,
coordinate axis labels) mirroring main.py's MAP_CSS_TEMPLATE, for one visual language across
every frontend.
"""

from __future__ import annotations

import io
import math
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

AXIS_LABEL_PADDING = 6  # outer padding between an axis label's text and the edge of the canvas
AXIS_TICK_LENGTH = 5  # short mark connecting a label to the exact column/row it's naming
AXIS_TICK_TEXT_GAP = 3  # gap between the label text and its tick mark
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

# render_heatmap's color ramp - low-to-high stops (dark navy -> blue -> teal -> amber -> red),
# the same general shape as common "heat" gradients (e.g. matplotlib's inferno/turbo) rather than
# a single-hue fade, since a multi-hue ramp is easier to read at a glance than shades of one color.
HEATMAP_STOPS: list[tuple[float, tuple[int, int, int]]] = [
    (0.0, (24, 28, 46)),
    (0.25, (43, 71, 130)),
    (0.5, (39, 138, 122)),
    (0.75, (231, 180, 62)),
    (1.0, (224, 64, 48)),
]
HEATMAP_OTHER_COUNTRY_COLOR = (48, 48, 44)  # muted context for tiles outside the target country
SCALE_BAR_WIDTH = 220
SCALE_BAR_HEIGHT = 12
SCALE_BAR_TOP_MARGIN = 20
SCALE_LABEL_FONT_SIZE = 12

# render_terrain's palette - one fixed color per Terrain value, chosen to read intuitively at a
# glance (greens for vegetation, blue for water, sand for desert, gray tones for bare rock/urban)
# rather than matching any in-game mechanical color scheme.
TERRAIN_COLORS: dict[str, tuple[int, int, int]] = {
    "PLAINS": (168, 194, 86),
    "FOREST": (45, 90, 39),
    "HILLS": (163, 148, 82),
    "MOUNTAIN": (120, 112, 102),
    "DESERT": (224, 192, 104),
    "WATER": (47, 111, 168),
    "URBAN": (150, 150, 165),
}
TERRAIN_OTHER_COUNTRY_COLOR = (48, 48, 44)  # same muted treatment as HEATMAP_OTHER_COUNTRY_COLOR

# render_terrain's division-marker overlay: one dot per country's deployment on a tile (both the
# target country's own and any other country's, visible within the crop - reading a border means
# seeing both sides), colored by that country's assign_country_colors() shade (same palette /map
# uses) and sized by that deployment's total manpower relative to the largest one in view, so a
# glance shows not just "forces here" but roughly how much. A short type abbreviation (see
# DIVISION_TYPE_ABBR) for the deployment's dominant type - same "most manpower" rule
# main.py's _dominant_type() uses for combat matchups - renders on top once the tile is large
# enough to actually fit text; a "xN" suffix appears too if more than one division is stacked on
# that tile.
DIVISION_MARKER_MIN_RADIUS_FRAC = 0.16
DIVISION_MARKER_MAX_RADIUS_FRAC = 0.42
DIVISION_MARKER_OUTLINE = (18, 18, 20)
DIVISION_MARKER_TEXT_MIN_TILE_SIZE = 16
DIVISION_MARKER_FONT_SIZE = 9
DIVISION_TYPE_ABBR: dict[str, str] = {
    "INFANTRY": "INF",
    "ARMOR": "ARM",
    "ARTILLERY": "ART",
    "CAVALRY": "CAV",
    "AIRBORNE": "ABN",
    "ENGINEER": "ENG",
    "LOGISTICS": "LOG",
    "AIR_FORCE": "AIR",
}


def _heat_color(t: float) -> tuple[int, int, int]:
    """Linear-interpolate HEATMAP_STOPS at t in [0, 1]."""
    t = max(0.0, min(1.0, t))
    for (t0, c0), (t1, c1) in zip(HEATMAP_STOPS, HEATMAP_STOPS[1:]):
        if t <= t1:
            local_t = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
            return tuple(round(c0[i] + (c1[i] - c0[i]) * local_t) for i in range(3))
    return HEATMAP_STOPS[-1][1]


def _format_compact(n: float) -> str:
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return f"{n:.0f}"


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


def _axis_margins(
    y_labels: list[int], axis_font: ImageFont.FreeTypeFont, scratch: ImageDraw.ImageDraw
) -> tuple[int, float]:
    """(y_axis_width, x_axis_height) - the margin space to reserve for coordinate axis labels
    (text + tick mark) along the left and top edges of the grid, shared by every renderer that
    draws axis labels (see _draw_axis_labels)."""
    y_label_text_width = max(scratch.textlength(str(v), font=axis_font) for v in y_labels)
    y_axis_width = round(AXIS_LABEL_PADDING + y_label_text_width + AXIS_TICK_TEXT_GAP + AXIS_TICK_LENGTH)
    x_axis_height = AXIS_LABEL_PADDING + AXIS_FONT_SIZE + AXIS_TICK_TEXT_GAP + AXIS_TICK_LENGTH
    return y_axis_width, x_axis_height


def _draw_axis_labels(
    draw: ImageDraw.ImageDraw,
    scratch: ImageDraw.ImageDraw,
    axis_font: ImageFont.FreeTypeFont,
    x_labels: list[int],
    y_labels: list[int],
    min_x: int,
    min_y: int,
    tile_stride: float,
    tile_size: int,
    grid_left: float,
    grid_top: float,
    x_axis_height: float,
    y_axis_width: int,
) -> None:
    """Coordinate numbers along the top and left edges of the grid, each with a short tick mark
    running to the exact column/row it names - without one it's ambiguous which tile a label
    lines up with, especially once labels are spaced several tiles apart (see _axis_label_step)."""

    def tile_center(x: int, y: int) -> tuple[float, float]:
        return (
            grid_left + (x - min_x) * tile_stride + tile_size / 2,
            grid_top + (y - min_y) * tile_stride + tile_size / 2,
        )

    for label_x in x_labels:
        cx, _ = tile_center(label_x, min_y)
        text = str(label_x)
        draw.line([(cx, grid_top - AXIS_TICK_LENGTH), (cx, grid_top)], fill=AXIS_COLOR, width=1)
        draw.text(
            (cx - scratch.textlength(text, font=axis_font) / 2, grid_top - x_axis_height + AXIS_LABEL_PADDING / 2),
            text,
            font=axis_font,
            fill=AXIS_COLOR,
        )
    for label_y in y_labels:
        _, cy = tile_center(min_x, label_y)
        text = str(label_y)
        draw.line([(grid_left - AXIS_TICK_LENGTH, cy), (grid_left, cy)], fill=AXIS_COLOR, width=1)
        draw.text(
            (grid_left - y_axis_width + AXIS_LABEL_PADDING, cy - AXIS_FONT_SIZE / 2),
            text,
            font=axis_font,
            fill=AXIS_COLOR,
        )


def _visible_deployments(
    world: World, min_x: int, min_y: int, max_x: int, max_y: int
) -> dict[tuple[int, int], list[tuple[str, int, str, int]]]:
    """(x, y) -> [(country, total_manpower, dominant_type_name, division_count), ...] for every
    node in bounds with at least one non-empty MilitaryDeployment - one entry per country
    garrisoning that tile (almost always just one, but a contested tile mid-fight could have
    more). Used by render_terrain's division-marker overlay."""
    result: dict[tuple[int, int], list[tuple[str, int, str, int]]] = {}
    for node in world.nodes.values():
        if not (min_x <= node.x <= max_x and min_y <= node.y <= max_y):
            continue
        entries = []
        for deployment in node.military_deployments:
            if not deployment.divisions:
                continue
            total_manpower = sum(d.manpower for d in deployment.divisions)
            dominant = game._dominant_type(deployment.divisions)
            entries.append((deployment.country, total_manpower, dominant.name if dominant else "", len(deployment.divisions)))
        if entries:
            result[(node.x, node.y)] = entries
    return result


def _draw_division_marker(
    draw: ImageDraw.ImageDraw,
    scratch: ImageDraw.ImageDraw,
    marker_font: ImageFont.FreeTypeFont,
    center: tuple[float, float],
    radius: float,
    color: tuple[int, int, int],
    dominant_type: str,
    division_count: int,
    tile_size: int,
) -> None:
    cx, cy = center
    draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=color, outline=DIVISION_MARKER_OUTLINE, width=1)
    if tile_size < DIVISION_MARKER_TEXT_MIN_TILE_SIZE:
        return
    label = DIVISION_TYPE_ABBR.get(dominant_type, "?")
    if division_count > 1:
        label += f"×{division_count}"
    text_width = scratch.textlength(label, font=marker_font)
    # Dark or light label text depending on the marker's own brightness, so it stays legible
    # against any of assign_country_colors()'s palette rather than just the darker half of it.
    brightness = 0.299 * color[0] + 0.587 * color[1] + 0.114 * color[2]
    text_color = (20, 20, 20) if brightness > 140 else (240, 240, 240)
    draw.text((cx - text_width / 2, cy - DIVISION_MARKER_FONT_SIZE / 2), label, font=marker_font, fill=text_color)


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
    y_axis_width, x_axis_height = _axis_margins(y_labels, axis_font, scratch)

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

    _draw_axis_labels(
        draw, scratch, axis_font, x_labels, y_labels, min_x, min_y, tile_stride, tile_size,
        grid_left, grid_top, x_axis_height, y_axis_width,
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


def render_heatmap(
    world: World, country_name: str, metric: str = "population", title: Optional[str] = None
) -> io.BytesIO:
    """A PNG heatmap of one country's own territory: each of its tiles colored along
    HEATMAP_STOPS by its population or GDP, relative to that country's own highest tile (not a
    world-wide scale - a small country and a huge one are each shown at their own full contrast).
    The value is sqrt-scaled before mapping to a color, the same perceptual-contrast trick
    generate_world.py's own tile coloring used - a plain linear scale would leave all but the
    single brightest tile looking nearly identical, since population/GDP within a country
    typically follows a long-tailed distribution (a handful of dense "cities" over a much larger
    number of sparser tiles). Tiles belonging to other countries (or unclaimed) within the
    cropped region are shown muted, and empty grid slots stay the standard background color, so
    the target country's own shape and hot spots are what actually stands out.

    `metric` must be `"population"` or `"gdp"`. Raises ValueError if the country doesn't exist,
    owns no nodes (via _country_bounds), or every one of its nodes has zero of the chosen metric
    (nothing to scale a heatmap against). Returns a ready-to-send BytesIO, already seeked to the
    start."""
    if metric not in ("population", "gdp"):
        raise ValueError(f"Unknown metric '{metric}' - use 'population' or 'gdp'.")

    country = world.countries.get(country_name)
    if country is None:
        raise ValueError(f"No such country '{country_name}'.")

    values: dict[tuple[int, int], float] = {}
    for node_id in country.nodes:
        node = world.nodes.get(node_id)
        if node is None:
            continue
        values[(node.x, node.y)] = node.population if metric == "population" else node.economic_output
    if not values:
        raise ValueError(f"'{country_name}' has no nodes.")
    max_value = max(values.values())
    if max_value <= 0:
        raise ValueError(f"'{country_name}' has no {metric} to show a heatmap of.")

    min_x, min_y, max_x, max_y = _country_bounds(world, country_name)
    region_width = max_x - min_x + 1
    region_height = max_y - min_y + 1
    metric_label = "Population" if metric == "population" else "GDP"
    title = f"{title} - {country_name} ({metric_label})" if title else f"{country_name} ({metric_label})"

    tile_size = max(4, min(32, 2000 // max(region_width, region_height, 1)))
    other_color = HEATMAP_OTHER_COUNTRY_COLOR
    empty_color = _hex_to_rgb(game.MAP_EMPTY_COLOR)
    grid = {(n.x, n.y): n for n in world.nodes.values()}

    title_font = ImageFont.load_default(size=TITLE_FONT_SIZE)
    badge_font = ImageFont.load_default(size=BADGE_FONT_SIZE)
    axis_font = ImageFont.load_default(size=AXIS_FONT_SIZE)
    scale_font = ImageFont.load_default(size=SCALE_LABEL_FONT_SIZE)

    map_width = region_width * tile_size + (region_width - 1) * TILE_GAP
    map_height = region_height * tile_size + (region_height - 1) * TILE_GAP

    tile_stride = tile_size + TILE_GAP
    x_labels = _axis_label_positions(min_x, max_x, _axis_label_step(region_width, tile_stride))
    y_labels = _axis_label_positions(min_y, max_y, _axis_label_step(region_height, tile_stride))

    scratch = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    y_axis_width, x_axis_height = _axis_margins(y_labels, axis_font, scratch)

    content_width = y_axis_width + map_width
    content_height = x_axis_height + map_height
    # Metric label above the gradient bar, endpoint values below it.
    scale_height = SCALE_BAR_TOP_MARGIN + SCALE_LABEL_FONT_SIZE + 4 + SCALE_BAR_HEIGHT + 4 + SCALE_LABEL_FONT_SIZE

    year_text = f"Year {world.year}"
    badge_width = scratch.textlength(year_text, font=badge_font) + BADGE_PADDING_X * 2
    badge_height = BADGE_FONT_SIZE + BADGE_PADDING_Y
    title_width = scratch.textlength(title, font=title_font) if title else 0
    header_width = title_width + (30 if title else 0) + badge_width

    canvas_width = round(max(content_width, header_width) + PAGE_PADDING * 2)
    canvas_height = PAGE_PADDING + HEADER_HEIGHT + content_height + scale_height + PAGE_PADDING

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

    content_left = (canvas_width - content_width) / 2
    grid_left = content_left + y_axis_width
    grid_top = PAGE_PADDING + HEADER_HEIGHT + x_axis_height

    _draw_axis_labels(
        draw, scratch, axis_font, x_labels, y_labels, min_x, min_y, tile_stride, tile_size,
        grid_left, grid_top, x_axis_height, y_axis_width,
    )

    for y in range(min_y, max_y + 1):
        for x in range(min_x, max_x + 1):
            value = values.get((x, y))
            if value is not None:
                color = _heat_color(math.sqrt(value / max_value))
            elif (x, y) in grid:
                color = other_color
            else:
                color = empty_color
            left = grid_left + (x - min_x) * tile_stride
            top = grid_top + (y - min_y) * tile_stride
            draw.rectangle([left, top, left + tile_size - 1, top + tile_size - 1], fill=color)

    scale_left = grid_left
    scale_top = grid_top + map_height + SCALE_BAR_TOP_MARGIN
    draw.text((scale_left, scale_top), metric_label, font=scale_font, fill=TEXT_COLOR)
    bar_top = scale_top + SCALE_LABEL_FONT_SIZE + 4
    for i in range(SCALE_BAR_WIDTH):
        t = i / (SCALE_BAR_WIDTH - 1)
        draw.line([(scale_left + i, bar_top), (scale_left + i, bar_top + SCALE_BAR_HEIGHT)], fill=_heat_color(t))
    draw.rectangle(
        [scale_left, bar_top, scale_left + SCALE_BAR_WIDTH, bar_top + SCALE_BAR_HEIGHT], outline=AXIS_COLOR, width=1
    )
    value_prefix = "$" if metric == "gdp" else ""
    high_text = f"{value_prefix}{_format_compact(max_value)}"
    draw.text((scale_left, bar_top + SCALE_BAR_HEIGHT + 4), f"{value_prefix}0", font=scale_font, fill=TEXT_COLOR)
    draw.text(
        (scale_left + SCALE_BAR_WIDTH - scratch.textlength(high_text, font=scale_font), bar_top + SCALE_BAR_HEIGHT + 4),
        high_text,
        font=scale_font,
        fill=TEXT_COLOR,
    )

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


def render_terrain(world: World, country_name: str, title: Optional[str] = None) -> io.BytesIO:
    """A PNG of one country's own territory colored by Terrain (see TERRAIN_COLORS) instead of by
    owning country or by population/GDP - useful for reading a border at a glance the way
    TERRAIN_TYPE_MODIFIERS/MATCHUP_MODIFIERS actually do (armor wants the plains/desert tiles,
    infantry wants the forest/mountain/urban ones). Tiles belonging to other countries within the
    cropped region are shown muted (same treatment as render_heatmap), and empty grid slots stay
    the standard background color, so the target country's own shape is what stands out. Raises
    ValueError (via _country_bounds) if the country doesn't exist or owns no nodes. Returns a
    ready-to-send BytesIO, already seeked to the start."""
    country = world.countries.get(country_name)
    if country is None:
        raise ValueError(f"No such country '{country_name}'.")

    min_x, min_y, max_x, max_y = _country_bounds(world, country_name)
    region_width = max_x - min_x + 1
    region_height = max_y - min_y + 1
    title = f"{title} - {country_name} (Terrain)" if title else f"{country_name} (Terrain)"

    tile_size = max(4, min(32, 2000 // max(region_width, region_height, 1)))
    other_color = TERRAIN_OTHER_COUNTRY_COLOR
    empty_color = _hex_to_rgb(game.MAP_EMPTY_COLOR)
    grid = {(n.x, n.y): n for n in world.nodes.values()}
    own_cells = {(n.x, n.y): n for node_id in country.nodes if (n := world.nodes.get(node_id)) is not None}

    # Division-marker overlay: both the target country's own deployments and any other country's
    # visible within the crop, so a border reads as a whole rather than just one side of it.
    deployments = _visible_deployments(world, min_x, min_y, max_x, max_y)
    country_colors = {name: _hex_to_rgb(color) for name, color in game.assign_country_colors(world).items()}
    max_manpower = max(
        (manpower for entries in deployments.values() for (_, manpower, _, _) in entries), default=0
    )

    title_font = ImageFont.load_default(size=TITLE_FONT_SIZE)
    badge_font = ImageFont.load_default(size=BADGE_FONT_SIZE)
    legend_font = ImageFont.load_default(size=LEGEND_FONT_SIZE)
    axis_font = ImageFont.load_default(size=AXIS_FONT_SIZE)
    marker_font = ImageFont.load_default(size=DIVISION_MARKER_FONT_SIZE)

    map_width = region_width * tile_size + (region_width - 1) * TILE_GAP
    map_height = region_height * tile_size + (region_height - 1) * TILE_GAP

    tile_stride = tile_size + TILE_GAP
    x_labels = _axis_label_positions(min_x, max_x, _axis_label_step(region_width, tile_stride))
    y_labels = _axis_label_positions(min_y, max_y, _axis_label_step(region_height, tile_stride))

    scratch = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    y_axis_width, x_axis_height = _axis_margins(y_labels, axis_font, scratch)

    content_width = y_axis_width + map_width
    content_height = x_axis_height + map_height

    legend_names = sorted({node.terrain.name for node in own_cells.values()})
    legend_rows = _wrap_legend(legend_names, legend_font, map_width)
    legend_height = LEGEND_TOP_MARGIN + len(legend_rows) * LEGEND_ROW_HEIGHT if legend_rows else 0

    division_country_names = sorted({country for entries in deployments.values() for (country, _, _, _) in entries})
    division_legend_rows = _wrap_legend(division_country_names, legend_font, map_width)
    division_legend_height = (
        LEGEND_TOP_MARGIN + LEGEND_FONT_SIZE + 4 + len(division_legend_rows) * LEGEND_ROW_HEIGHT
        if division_legend_rows
        else 0
    )

    year_text = f"Year {world.year}"
    badge_width = scratch.textlength(year_text, font=badge_font) + BADGE_PADDING_X * 2
    badge_height = BADGE_FONT_SIZE + BADGE_PADDING_Y
    title_width = scratch.textlength(title, font=title_font) if title else 0
    header_width = title_width + (30 if title else 0) + badge_width

    canvas_width = round(max(content_width, header_width) + PAGE_PADDING * 2)
    canvas_height = (
        PAGE_PADDING + HEADER_HEIGHT + content_height + legend_height + division_legend_height + PAGE_PADDING
    )

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

    content_left = (canvas_width - content_width) / 2
    grid_left = content_left + y_axis_width
    grid_top = PAGE_PADDING + HEADER_HEIGHT + x_axis_height

    _draw_axis_labels(
        draw, scratch, axis_font, x_labels, y_labels, min_x, min_y, tile_stride, tile_size,
        grid_left, grid_top, x_axis_height, y_axis_width,
    )

    for y in range(min_y, max_y + 1):
        for x in range(min_x, max_x + 1):
            node = own_cells.get((x, y))
            if node is not None:
                color = TERRAIN_COLORS.get(node.terrain.name, other_color)
            elif (x, y) in grid:
                color = other_color
            else:
                color = empty_color
            left = grid_left + (x - min_x) * tile_stride
            top = grid_top + (y - min_y) * tile_stride
            draw.rectangle([left, top, left + tile_size - 1, top + tile_size - 1], fill=color)

            entries = deployments.get((x, y))
            if not entries:
                continue
            # More than one country garrisoning the same tile is rare (a contested tile
            # mid-fight), but when it happens, lay their markers out side by side instead of
            # stacking them fully on top of each other.
            slot_width = tile_size / len(entries)
            for i, (dep_country, manpower, dominant_type, division_count) in enumerate(entries):
                slot_center_x = left + slot_width * (i + 0.5)
                slot_center_y = top + tile_size / 2
                size_t = math.sqrt(manpower / max_manpower) if max_manpower > 0 else 0.0
                radius = (
                    DIVISION_MARKER_MIN_RADIUS_FRAC
                    + (DIVISION_MARKER_MAX_RADIUS_FRAC - DIVISION_MARKER_MIN_RADIUS_FRAC) * size_t
                ) * min(tile_size, slot_width)
                marker_color = country_colors.get(dep_country, TEXT_COLOR)
                _draw_division_marker(
                    draw, scratch, marker_font, (slot_center_x, slot_center_y), radius, marker_color,
                    dominant_type, division_count, tile_size,
                )

    legend_y = grid_top + map_height + LEGEND_TOP_MARGIN
    for row in legend_rows:
        x = grid_left
        for name in row:
            draw.rounded_rectangle(
                [x, legend_y + 4, x + LEGEND_SWATCH_SIZE, legend_y + 4 + LEGEND_SWATCH_SIZE],
                radius=2,
                fill=TERRAIN_COLORS.get(name, other_color),
            )
            draw.text((x + LEGEND_SWATCH_SIZE + 6, legend_y), name.title(), font=legend_font, fill=TEXT_COLOR)
            x += LEGEND_SWATCH_SIZE + 6 + draw.textlength(name.title(), font=legend_font) + LEGEND_ITEM_GAP
        legend_y += LEGEND_ROW_HEIGHT

    if division_legend_rows:
        legend_y += LEGEND_TOP_MARGIN
        draw.text((grid_left, legend_y), "Divisions", font=legend_font, fill=TEXT_COLOR)
        legend_y += LEGEND_FONT_SIZE + 4
        for row in division_legend_rows:
            x = grid_left
            for name in row:
                draw.ellipse(
                    [x, legend_y + 3, x + LEGEND_SWATCH_SIZE, legend_y + 3 + LEGEND_SWATCH_SIZE],
                    fill=country_colors.get(name, TEXT_COLOR),
                    outline=DIVISION_MARKER_OUTLINE,
                    width=1,
                )
                draw.text((x + LEGEND_SWATCH_SIZE + 6, legend_y), name, font=legend_font, fill=TEXT_COLOR)
                x += LEGEND_SWATCH_SIZE + 6 + draw.textlength(name, font=legend_font) + LEGEND_ITEM_GAP
            legend_y += LEGEND_ROW_HEIGHT

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer
