"""Print-in-place chain

Parametric 3D-printable interlocking chains.
"""

from __future__ import annotations

import math
from typing import Callable, ClassVar, Literal

from build123d import *  # pyright: ignore[reportWildcardImportFromLibrary]
from proto import *  # pyright: ignore[reportWildcardImportFromLibrary]

__author__ = "jsmnbom"
__urls__ = {
  "Printables": "https://www.printables.com/@jsmnbom",
}


# --------------------------------------------------------------------------- #
# Choice types — link outlines and cross-sections
# --------------------------------------------------------------------------- #
#
# These are both form types (Parameters fields, with SVG previews) and geometry
# builders (ChainLink/Chain sweep along / around them), which is why the design
# stays one interconnected module.


ChainLinkOutlineBuilder = Callable[[float, float], "Wire"]


class ChainLinkOutline(Choice[ChainLinkOutlineBuilder]):
  """Link centerline outlines. Each builds a closed ``Wire`` from the half-extents
  ``(cx, cy)`` — used both as the sweep path and, at icon size, the form preview.
  Returns a ``Wire`` (not a raw BuildLine ``Curve``); ExportSVG IndexErrors on a
  straight-line ``Curve``."""

  @member
  @staticmethod
  def rect(cx: float, cy: float) -> Wire:
    """Rectangle

    Sharp 90° corners — prints best, but looks ugly."""
    with BuildLine() as bl:
      Polyline((cx, cy), (-cx, cy), (-cx, -cy), (cx, -cy), close=True)
    return bl.wire()

  @member
  @staticmethod
  def chamfered(cx: float, cy: float) -> Wire:
    """Chamfered rect

    Corners cut flat at 45° — prints well and looks better than a rectangle."""
    cl = min(cx, cy) * 0.5  # cut-back distance per corner
    with BuildLine() as bl:
      Polyline(
        (cx, cy - cl),
        (cx - cl, cy),
        (-cx + cl, cy),
        (-cx, cy - cl),
        (-cx, -cy + cl),
        (-cx + cl, -cy),
        (cx - cl, -cy),
        (cx, -cy + cl),
        close=True,
      )
    return bl.wire()

  @member
  @staticmethod
  def rounded(cx: float, cy: float) -> Wire:
    """Rounded rect

    Smoothly filleted corners — prints poorly but theoretically looks best."""
    with BuildLine() as bl:
      FilletPolyline(
        (cx, cy), (-cx, cy), (-cx, -cy), (cx, -cy), radius=min(cx, cy) * 0.5, close=True
      )
    return bl.wire()


ChainLinkCrossSectionBuilder = Callable[[float], RegularPolygon]


class ChainLinkCrossSection(Choice[ChainLinkCrossSectionBuilder]):
  """Swept cross-sections — regular polygons. Each returns the ``RegularPolygon``
  at circumradius ``radius``; ``.side_count`` fixes the interlock tilt."""

  @member
  @staticmethod
  def square(radius: float) -> RegularPolygon:
    """Square

    4 sides — chunky, angular links."""
    return RegularPolygon(radius=radius, side_count=4, rotation=90)

  @member
  @staticmethod
  def hexagon(radius: float) -> RegularPolygon:
    """Hexagon

    6 sides — a middle ground."""
    return RegularPolygon(radius=radius, side_count=6, rotation=90)

  @member
  @staticmethod
  def octagon(radius: float) -> RegularPolygon:
    """Octagon

    8 sides — smooth and reliable, the default."""
    return RegularPolygon(radius=radius, side_count=8, rotation=90)

  @member
  @staticmethod
  def decagon(radius: float) -> RegularPolygon:
    """Decagon

    10 sides — rounder; unlocks the tilt multiplier."""
    return RegularPolygon(radius=radius, side_count=10, rotation=90)

  @member
  @staticmethod
  def dodecagon(radius: float) -> RegularPolygon:
    """Dodecagon

    12 sides — roundest, with the most tilt range."""
    return RegularPolygon(radius=radius, side_count=12, rotation=90)


# A link tilts ``(360/sides) * (tilt_mult - 0.5)``; at 90° it stands vertical and
# can no longer interlock flat, so each cross-section allows the multipliers that
# stay under that. Below a decagon only 1 is possible, so the control is hidden
# there entirely (see tilt_mult's `show_if`) and a hidden value stays inert at 1 —
# mirroring how `brim_diameter` is ignored unless `brim == "ears"`. This is the
# single source of truth for both the slider range and the normalisation in
# `Parameters._check_relations`.
_TILT_MULT_MAX = {
  "decagon": 2,
  "dodecagon": 3,
}


# --------------------------------------------------------------------------- #
# Layout previews — schematic outline wires for the line/spiral cards.
# --------------------------------------------------------------------------- #
#
# Like the Choice previews, these are exported straight to SVG from a real
# build123d ``Wire`` so the layout cards render actual geometry rather than a
# hand-drawn icon. They are schematic (a tilted-link run vs. an Archimedean coil),
# not the built chain itself.


def _line_preview() -> Wire:
  """A short run of alternately tilted links, suggesting the straight layout."""
  with BuildLine() as bl:
    Polyline((-3, 0.5), (-1.8, -0.5), (-0.6, 0.5), (0.6, -0.5), (1.8, 0.5), (3, -0.5))
  return bl.wire()


def _spiral_preview() -> Wire:
  """A flat Archimedean coil, suggesting the spiral layout."""
  turns, samples = 2.5, 80
  pts = []
  for i in range(samples + 1):
    t = turns * 2 * math.pi * i / samples
    r = 0.16 * t
    pts.append((r * math.cos(t), r * math.sin(t)))
  with BuildLine() as bl:
    Polyline(*pts)
  return bl.wire()


# --------------------------------------------------------------------------- #
# Parameters — the form/validation source of truth
# --------------------------------------------------------------------------- #


class Parameters(BaseModel):
  """Parameters for a chain of interlocking links (all lengths in mm).

  Field order is the form's order, and the first three are the ``preset_stable``
  ones — how much chain and how it's arranged, which is the user's call. The
  ``PRESETS`` below own everything after them (one link shape each), so the form
  can rule off where a preset's territory starts."""

  link_count: int = SliderField(
    default=6,
    name="Link count",
    ge=1,
    le=500,
    slider_max=200,
    step=1,
    preset_stable=True,
  )
  """Number of links in the chain."""

  layout: Literal["line", "spiral"] = CardsField(
    default="line",
    name="Layout",
    choices=[
      Option("line", "Line", "Links in a straight row.", wire=_line_preview()),
      Option(
        "spiral", "Spiral", "Links curled into a flat coil.", wire=_spiral_preview()
      ),
    ],
    preset_stable=True,
    # A line and a spiral of the same links occupy wildly different footprints,
    # so hold no camera across the switch — reframe on the new one.
    recenter=True,
  )
  """Arrange the links in a straight line or a flat Archimedean spiral. Both print
  flat and interlock the same way."""

  spiral_skip: int = SliderField(
    default=20,
    name="Center skip",
    ge=0,
    le=500,
    slider_max=200,
    step=1,
    show_if={"layout": "spiral"},
    size="xs",
    preset_stable=True,
  )
  """How many link-slots to skip at the tight center of the spiral — raise it to
  open up the innermost turns."""

  link_shape: ChainLinkOutline = ChoiceField(
    default=ChainLinkOutline.chamfered,
    name="Link shape",
    choices=ChainLinkOutline,
    preview=lambda m: m(2.0, 1.2),  # representative icon size
    size="lg",
  )
  """The centerline the link's bar is swept along."""

  cross_section: ChainLinkCrossSection = ChoiceField(
    default=ChainLinkCrossSection.octagon,
    name="Cross-section",
    choices=ChainLinkCrossSection,
    preview=lambda m: m(1.0),  # representative icon size
    size="lg",
  )
  """The bar's profile. More sides look rounder and allow a finer interlock tilt."""

  link_length: float = SliderField(
    default=25.0,
    name="Link length",
    gt=0,
    le=120.0,
    slider_min=5,
    slider_max=80,
    step=0.5,
    unit="mm",
  )
  """Long axis of each link. Must be at least the link width."""

  link_width: float = SliderField(
    default=14.0,
    name="Link width",
    gt=0,
    le=60.0,
    slider_min=3,
    slider_max=40,
    step=0.5,
    unit="mm",
  )
  """Short axis of each link. Must be at least the thickness."""

  link_thickness: float = SliderField(
    default=3.0,
    name="Link thickness",
    gt=0,
    le=20.0,
    slider_min=0.5,
    slider_max=15,
    step=0.1,
    unit="mm",
  )
  """Bar diameter of each link — thinner links open a print gap between
  neighbours; too thick and they fuse."""

  tilt_mult: int = SliderField(
    default=1,
    name="Tilt multiplier",
    ge=1,
    le=max(_TILT_MULT_MAX.values()),
    step=1,
    # Only shown for cross-sections with enough faces; the slider max then depends
    # on which one is selected (see _TILT_MULT_MAX).
    show_if={"cross_section": list(_TILT_MULT_MAX)},
    slider_max_by={"cross_section": _TILT_MULT_MAX},
    size="xs",
  )
  """How many cross-section faces to skip when tilting each link."""

  brim: Literal["none", "ears"] = SelectField(
    default="none",
    name="Brim",
    choices=[
      Option("none", "None", "No brim."),
      Option("ears", "Mouse ears", "Thin discs at each link's contact points."),
    ],
  )
  """Adds small discs under each link to widen bed contact and prevent lifting on
  hard-to-print settings."""

  brim_diameter: float = SliderField(
    default=8.0,
    name="Brim diameter",
    gt=0,
    le=30.0,
    slider_min=4,
    slider_max=15,
    step=0.5,
    unit="mm",
    show_if={"brim": "ears"},
    size="xs",
  )
  """Diameter of each mouse-ear disc."""

  @model_validator(mode="after")
  def _check_relations(self) -> "Parameters":
    if self.link_width < self.link_thickness:
      raise ValueError("link_width must be >= link_thickness")
    if self.link_length < self.link_width:
      raise ValueError("link_length must be >= link_width")
    # tilt_mult's usable range depends on cross_section (see _TILT_MULT_MAX). The
    # form's slider already caps it, but a value left over from a since-changed
    # cross-section can be stale, so normalise it here — this keeps the guarantee
    # in the model and lets Chain trust its params. Clamped (not rejected) so a
    # hidden field stays inert, mirroring brim_diameter when brim != "ears".
    max_tilt_mult = _TILT_MULT_MAX.get(self.cross_section.value, 1)
    self.tilt_mult = min(self.tilt_mult, max_tilt_mult)
    return self


# --------------------------------------------------------------------------- #
# Geometry
# --------------------------------------------------------------------------- #


class ChainLink(BasePartObject):
  """A single link: a regular-polygon cross-section swept along a named
  centerline outline (a ``ChainLinkOutline``)."""

  _applies_to = [BuildPart._tag]

  def __init__(
    self,
    length: float = 30.0,
    width: float = 12.0,
    thickness: float = 4.0,
    outline: ChainLinkOutlineBuilder = ChainLinkOutline.chamfered,
    cross_section: ChainLinkCrossSectionBuilder = ChainLinkCrossSection.octagon,
    rotation: RotationLike = (0, 0, 0),
    align: Align | tuple[Align, Align, Align] = Align.CENTER,
    mode: Mode = Mode.ADD,
  ):
    if thickness <= 0:
      raise ValueError("thickness must be positive")
    if width < thickness:
      raise ValueError("width must be >= thickness")
    if length < width:
      raise ValueError("length must be >= width")

    cx = length / 2 - thickness / 2  # half-width of centerline outline in X
    cy = width / 2 - thickness / 2  # half-width of centerline outline in Y
    path = outline(cx, cy)  # closed centerline wire for this outline
    radius = thickness / 2

    # Regular-polygon cross-section swept along the path.
    with BuildPart() as link:
      with BuildSketch(Plane(origin=path @ 0, z_dir=path % 0)):
        cs = cross_section(radius)
        self.side_count = cs.side_count
      sweep(path=path, is_frenet=False, transition=Transition.RIGHT)

    assert link.part is not None
    link.part.label = "chain_link"

    super().__init__(part=link.part, rotation=rotation, align=align, mode=mode)


class Chain(Design[Parameters]):
  """A line of interlocking links tiled along X with alternating tilt so
  successive links interlock. Instantiating with a ``Parameters`` builds the
  geometry into ``self`` (a ``Compound``)."""

  # Each preset is one link *shape* and nothing else: it sets no ``preset_stable``
  # field (they are dropped on the way out), so picking one never changes how many
  # links you asked for or whether they run in a line or a coil. Everything a
  # preset doesn't name keeps the field default.
  PRESET_LABEL: ClassVar[str] = "Link shape"
  PRESETS: ClassVar[list[Preset[Parameters]]] = [
    Preset(
      name="Default",
      description="Everyday chain — sturdy links at a comfortable size.",
      params=Parameters(),
    ),
    Preset(
      name="Smol",
      description="Jewellery scale, with mouse ears so the tiny links stay put.",
      params=Parameters(
        link_length=11.0,
        link_width=6.0,
        link_thickness=1.3,
        brim="ears",
        brim_diameter=4.0,
      ),
    ),
    Preset(
      name="Pretty",
      description="Almost rounded links.",
      params=Parameters(
        cross_section=ChainLinkCrossSection.dodecagon,
        link_length=27.0,
        link_width=17.0,
        link_thickness=4.8,
        tilt_mult=2,
      ),
    ),
    Preset(
      name="Large",
      description="Heavy statement links, twelve-sided.",
      params=Parameters(
        cross_section=ChainLinkCrossSection.dodecagon,
        link_length=60.0,
        link_width=35.0,
        link_thickness=10.0,
        tilt_mult=2,
      ),
    ),
  ]

  def __init__(self, params: Parameters):
    self.params = params
    count = params.link_count
    if count < 1:
      raise ValueError("count must be >= 1")

    # Build one link once; keep it out of the active context.
    self.link = ChainLink(
      length=params.link_length,
      width=params.link_width,
      thickness=params.link_thickness,
      outline=params.link_shape,
      cross_section=params.cross_section,
      mode=Mode.PRIVATE,
    )

    inner_long_edge_length = (
      self.link.edges()
      .filter_by(Axis.X)
      .filter_by(lambda e: e.start_point().Y > 0)
      .sort_by(SortBy.LENGTH)[0]
      .length
    )

    sides = self.link.side_count
    base_tilt = 360.0 / sides / 2
    face_step = 360.0 / sides
    self.tilt = base_tilt + (params.tilt_mult - 1) * face_step
    pitch = inner_long_edge_length

    # Per-link placement: (x, y, heading°). A line runs straight along X with no
    # heading; a spiral walks a flat Archimedean coil at the same arc-length pitch,
    # so both interlock identically (see _spiral_placements).
    if params.layout == "spiral":
      placements = self._spiral_placements(count, pitch, params.spiral_skip)
    else:
      placements = [(i * pitch, 0.0, 0.0) for i in range(count)]

    self.base_links: list[Compound] = []
    # Each link's long-axis direction in the bed plane — X for a line, the spiral
    # tangent otherwise. Used to lay mouse-ear brims along the actual contact run.
    self.link_dirs: list[Vector] = []
    for i, (x, y, heading) in enumerate(placements):
      rx = self.tilt if i % 2 else -self.tilt
      # Inner rotation is the line-mode tilt (long axis along local X, tilt about
      # X); the outer Z-rotation aligns local X to the spiral tangent (heading=0
      # for a line leaves it unchanged).
      loc = Location((x, y, 0), (0, 0, heading)) * Location((0, 0, 0), (90 + rx, 0, 0))
      self.base_links.append(self.link.moved(loc))
      self.link_dirs.append(
        Vector(math.cos(math.radians(heading)), math.sin(math.radians(heading)))
      )

    z_min = self.base_links[0].faces().sort_by(Axis.Z)[0].bounding_box().min.Z

    self.links = self._add_brim(
      z_min=z_min, brim=params.brim, brim_diameter=params.brim_diameter
    )

    super().__init__(None, label="chain", children=self.links)

  def _spiral_placements(
    self, count: int, pitch: float, skip: int
  ) -> list[tuple[float, float, float]]:
    """Walk a flat Archimedean spiral ``r(θ) = b·θ`` and return one ``(x, y,
    heading°)`` per link, spaced ``pitch`` apart in arc length. Slots ``0..skip-1``
    are skipped so the tight center (where a pitch step wraps too far) is left out.

    The arm spacing (radial gap per turn, ``2π·b``) is auto-derived from the tilted
    link's footprint perpendicular to travel, so neighbouring turns clear each other
    — the same "measure the real geometry" approach as ``pitch``."""
    # Radial extent of a tilted link (its Y footprint), plus a print clearance, is
    # the minimum a turn must advance so adjacent arms don't collide.
    tilted = self.link.moved(Location((0, 0, 0), (90 + self.tilt, 0, 0)))
    bb = tilted.bounding_box()
    arm_spacing = max(bb.size.Y + self.params.link_thickness, pitch)
    b = arm_spacing / (2 * math.pi)  # radius growth per radian

    total = skip + count
    targets = [k * pitch for k in range(1, total + 1)]  # arc length at each slot end

    placements: list[tuple[float, float, float]] = []
    theta = 0.0
    s = 0.0
    dtheta = 0.005  # rad; fine enough that a pitch step lands within tolerance
    next_i = 0
    while next_i < total:
      r = b * theta
      s += math.sqrt(r * r + b * b) * dtheta
      theta += dtheta
      while next_i < total and s >= targets[next_i]:
        r = b * theta
        x = r * math.cos(theta)
        y = r * math.sin(theta)
        # Spiral tangent angle (dx/dθ, dy/dθ) for r = b·θ.
        heading = math.degrees(
          math.atan2(
            b * math.sin(theta) + r * math.cos(theta),
            b * math.cos(theta) - r * math.sin(theta),
          )
        )
        if next_i >= skip:
          placements.append((x, y, heading))
        next_i += 1
    return placements

  def _add_brim(self, z_min: float, brim: str, brim_diameter: float) -> list[Compound]:
    if brim != "ears" or brim_diameter <= 0:
      return self.base_links

    links = []

    for link, direction in zip(self.base_links, self.link_dirs):
      # The link rests on the bed along the bottom of its straight run, which lies
      # along `direction` (X for a line, the spiral tangent otherwise). Find that
      # run's two ends by projecting the bottom face onto `direction` — orientation-
      # agnostic, so it's correct for a rotated (spiral) link, where the face's
      # global-X bbox extremes would sit off the run.
      bottom_face = link.faces().sort_by(Axis.Z)[0]
      c = bottom_face.center()
      center = Vector(c.X, c.Y)
      half_len = max(
        abs((Vector(v.X, v.Y) - center).dot(direction)) for v in bottom_face.vertices()
      )
      ears = [
        self._brim_ear(center, center + direction * s * half_len, z_min, brim_diameter)
        for s in (1, -1)
      ]
      links.append(link + ears)

    return links

  @staticmethod
  def _brim_ear(
    center: Vector, pos: Vector, z_min: float, brim_diameter: float
  ) -> Cylinder:
    """A single mouse-ear brim disc — one first layer tall — at the end of a
    link's bottom-contact run."""
    direction = center - pos
    pos -= direction.normalized() * (brim_diameter * 0.4)
    return Cylinder(radius=brim_diameter / 2, height=FIRST_LAYER_HEIGHT).moved(
      Location((pos.X, pos.Y, z_min + FIRST_LAYER_HEIGHT / 2))
    )

  # ------------------------------------------------------------------------- #
  # Printability analysis
  # ------------------------------------------------------------------------- #
  #
  # The chain is a row of near-vertical, alternately ±tilted loops. Each loop
  # touches the bed only along the bottom of its straight run; how much area that
  # is (a facet strip vs. a knife edge) decides whether the first layer sticks.
  # Every link has the same footprint and tilt (both layouts), so bed contact and
  # lean are one-link measurements. In line mode adjacent (and skip-one) pairs are
  # congruent; in spiral mode the innermost pairs (base_links[0]/[1]/[2]) have the
  # sharpest heading change and so bound the interlock — checking them is
  # conservative. Either way the analysis is O(1) regardless of link count.

  def analyze(self) -> Report:
    """Assess the built chain's printability (bed contact + link interlock + lean).

    The generic checks come from ``Report``; only the chain-specific choices —
    which face stands for the per-link footprint (periodicity), brim messaging,
    and the alternating-tilt lean — live here."""
    report = Report()

    # 0. Degenerate geometry — if the build collapsed to slivers / invalid solids the
    # measurements below are meaningless (and may raise), so bail early. Every link
    # is a rigid-transform copy of the same construction, so one stands for all —
    # the full BRep validity check is too costly to repeat per link.
    if not report.check_geometry(self, sample=self.links[:1]):
      return report

    # 1. Bed-contact footprint (one link stands for all, by periodicity). With mouse
    # ears on, only the effective (exported) footprint is worth reporting — the bare
    # link's is never what actually hits the bed.
    if self.params.brim == "ears":
      eff_area = self.links[0].faces().sort_by(Axis.Z)[0].area
      report.check_bed_contact(eff_area, label="Bed contact with mouse ears")
    else:
      bare_area = self.base_links[0].faces().sort_by(Axis.Z)[0].area
      report.check_bed_contact(
        bare_area,
        label="Bed contact per link",
        suggestion="Try the mouse-ears brim.",
      )

    # 2. Interlock: overlap (error) / clearance (warning) for one adjacent pair, plus
    # one skip-one pair — a link can also collide with its next-nearest neighbour by
    # squeezing past the one in between, which an adjacent-only check would miss.
    if len(self.base_links) >= 2:
      report.check_overlap(
        self.base_links[0],
        self.base_links[1],
        overlap_label="Links overlap",
        clearance_label="Clearance between links",
      )
    else:
      report.add(
        "Single link", 1, detail="Only one link — no interlock to check.", status="ok"
      )
    if len(self.base_links) >= 3:
      report.check_overlap(
        self.base_links[0],
        self.base_links[2],
        overlap_label="Links overlap (skip-one)",
        clearance_label="Clearance between skip-one links",
      )

    # 3. Floating sections — every link is bedded by construction, so this is a cheap
    # ok-guard, but it catches a future regression that lifts a link off the bed.
    report.check_floating(self)

    # 4. Lean — each link tilts ``self.tilt`` from vertical (mirror-alternated).
    # _TILT_MULT_MAX only holds the tilt below 90°, so it can pass the overhang
    # limit (e.g. a square cross-section's 45° base tilt), where the link's flank
    # becomes unsupported. Analytical from the known tilt — O(1), no geometry walk.
    self._check_lean(report)

    return report

  def _check_lean(self, report: Report) -> None:
    """Flag when a link leans past the overhang limit (unprintable without support)."""
    lean = self.tilt
    status = "error" if lean > MAX_OVERHANG_DEG else "ok"
    detail = (
      f"Links lean {lean:.0f}° from vertical — past {MAX_OVERHANG_DEG:.0f}° the flank "
      "overhangs and needs support. Lower the tilt multiplier or add cross-section faces."
      if status == "error"
      else "Links stay within the self-supporting overhang angle."
    )
    report.add("Link lean", round(lean, 1), unit="°", detail=detail, status=status)
