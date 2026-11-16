"""Infinity cube

Eight chamfered cubes on eight snap-fit hinges, folding through itself forever.
Prints as sixteen loose pieces and snaps together.
"""

from __future__ import annotations

import itertools
from typing import ClassVar, Literal

from build123d import *  # pyright: ignore[reportWildcardImportFromLibrary]
from proto import *  # pyright: ignore[reportWildcardImportFromLibrary]

__author__ = "jsmnbom"
__urls__ = {
  "Printables": "https://www.printables.com/@jsmnbom",
}
# Still being worked out — the hinge tolerances haven't been print-verified and the
# parameters may still move. Badges the gallery card and the generator header.
__experimental__ = True


# --------------------------------------------------------------------------- #
# Layout previews — the outlines drawn on the layout cards
# --------------------------------------------------------------------------- #


def _assembled_preview() -> Wire:
  """An isometric cube silhouette, suggesting the folded toy."""
  with BuildLine() as bl:
    Polyline(
      (0, 2), (1.73, 1), (1.73, -1), (0, -2), (-1.73, -1), (-1.73, 1), close=True
    )
  return bl.wire()


def _flat_preview() -> Wire:
  """A boustrophedon path through a 4x2 grid, suggesting the print bed."""
  with BuildLine() as bl:
    Polyline(
      (-2.4, 0.8),
      (2.4, 0.8),
      (2.4, -0.8),
      (-2.4, -0.8),
    )
  return bl.wire()


# --------------------------------------------------------------------------- #
# Parameters — the form/validation source of truth
# --------------------------------------------------------------------------- #


class Parameters(BaseModel):
  """Parameters for an infinity cube (all lengths in mm).

  Field order is the form's order. ``layout`` comes first and is the only
  ``preset_stable`` field — which arrangement you are looking at is your call,
  not a preset's, so it survives every switch. The ``PRESETS`` below own
  everything after it (one size each).

  The tolerances at the end are the ones worth printing a test piece for: they
  decide whether a connector snaps on and stays on."""

  layout: Literal["assembled", "flat"] = CardsField(
    default="flat",
    name="Layout",
    choices=[
      Option(
        "assembled",
        "Assembled",
        "The finished toy, hinges seated.",
        wire=_assembled_preview(),
      ),
      Option(
        "flat",
        "Print layout",
        "All sixteen pieces laid out on the bed.",
        wire=_flat_preview(),
      ),
    ],
    preset_stable=True,
    # The two arrangements occupy completely different footprints, so hold no
    # camera across the switch — reframe on the new one.
    recenter=True,
  )
  """Show the cube assembled, or the sixteen pieces laid out for printing. The
  exported 3MF always lands the pieces flat on the bed either way."""

  cube_size: float = SliderField(
    default=10.0,
    name="Cube size",
    gt=0,
    le=60.0,
    slider_min=5.0,
    slider_max=30.0,
    step=0.5,
    unit="mm",
  )
  """Edge length of one of the eight small cubes."""

  cube_chamfer: float = SliderField(
    default=1.0,
    name="Cube chamfer",
    gt=0,
    le=25.0,
    slider_min=0.2,
    slider_max=5.0,
    step=0.1,
    unit="mm",
  )
  """How far the cube's edges are cut back. Also sets how much of the face is
  left to hold the connector, so it feeds into the connector width."""

  connector_height: float = SliderField(
    default=4.2,
    name="Connector height",
    gt=0,
    le=25.0,
    slider_min=1.0,
    slider_max=10.0,
    step=0.1,
    unit="mm",
  )
  """Size of the square hinge pocket cut into each cube edge, and so the
  thickness of the connector that sits in it."""

  connector_width_ratio: float = SliderField(
    default=0.8,
    name="Connector width",
    gt=0,
    le=1.0,
    slider_min=0.3,
    slider_max=1.0,
    step=0.01,
  )
  """Fraction of the cube's flat face the connector spans. Wider is stronger;
  narrower leaves more of the cube face intact."""

  pivot_radius: float = SliderField(
    default=1.25,
    name="Pivot radius",
    gt=0,
    le=15.0,
    slider_min=0.3,
    slider_max=4.0,
    step=0.05,
    unit="mm",
  )
  """Radius of the dome the hinge turns on."""

  pivot_depth: float = SliderField(
    default=1.5,
    name="Pivot depth",
    gt=0,
    le=15.0,
    slider_min=0.3,
    slider_max=4.0,
    step=0.05,
    unit="mm",
  )
  """How far the pivot dome stands out from the pocket wall. Deeper holds
  better but needs a wider connector to snap over."""

  cube_spacing: float = SliderField(
    default=0.1,
    name="Cube spacing",
    gt=0,
    le=10.0,
    slider_min=0.05,
    slider_max=2.0,
    step=0.05,
    unit="mm",
  )
  """Gap left between neighbouring cubes so they can pivot without rubbing."""

  connector_tolerance: float = SliderField(
    default=0.1,
    name="Connector fit",
    ge=0,
    le=1.0,
    step=0.01,
    unit="mm",
  )
  """Clearance between the connector and the pocket it sits in."""

  connector_side_tolerance: float = SliderField(
    default=0.1,
    name="Connector side fit",
    ge=0,
    le=1.0,
    step=0.01,
    unit="mm",
  )
  """Clearance on the connector's two flat sides."""

  connector_snap_tolerance: float = SliderField(
    default=0.3,
    name="Snap interference",
    ge=0,
    le=1.0,
    step=0.01,
    unit="mm",
  )
  """How much smaller the socket's mouth is than the pivot it swallows. This is
  the snap: too little and the hinge falls apart, too much and the connector
  splits going on."""

  pivot_tolerance: float = SliderField(
    default=0.07,
    name="Pivot fit",
    ge=0,
    le=1.0,
    step=0.01,
    unit="mm",
  )
  """Clearance between the pivot dome and the seated socket, once snapped on."""

  @property
  def connector_width(self) -> float:
    """Span of the connector across the cube's face — the flat left between the
    two chamfers, scaled by ``connector_width_ratio``."""
    return (self.cube_size - self.cube_chamfer * 2) * self.connector_width_ratio

  @model_validator(mode="after")
  def _check_fits(self) -> "Parameters":
    if self.cube_chamfer * 2 >= self.cube_size:
      raise ValueError(
        "Cube chamfer is too large — twice the chamfer must stay under the cube size."
      )
    # The pocket profile reaches h + h/8 in from the edge; past half the cube it
    # would break out through the far side.
    if self.connector_height * 1.125 >= self.cube_size / 2:
      raise ValueError(
        "Connector height is too large for this cube — reduce it below "
        f"{self.cube_size / 2 / 1.125:.2f} mm or use a bigger cube."
      )
    if self.pivot_radius > self.connector_height / 2:
      raise ValueError(
        "Pivot radius must fit the hinge pocket — keep it at or under half the "
        f"connector height ({self.connector_height / 2:.2f} mm)."
      )
    if self.pivot_depth >= self.connector_width / 2:
      raise ValueError(
        "Pivot depth must stay under half the connector width "
        f"({self.connector_width / 2:.2f} mm)."
      )
    # The socket's mouth is built as PivotDome(radius - snap, depth - snap); at or
    # past either, the dome inverts and the build collapses.
    if self.connector_snap_tolerance >= min(self.pivot_radius, self.pivot_depth):
      raise ValueError(
        "Snap interference must stay under both the pivot radius and depth "
        f"({min(self.pivot_radius, self.pivot_depth):.2f} mm)."
      )
    return self


# --------------------------------------------------------------------------- #
# Geometry — three unique solids, everything else is a placed copy
# --------------------------------------------------------------------------- #
#
# Each part is built inside a *single* function frame, with its nested
# ``BuildSketch`` blocks in that same frame. build123d only adopts a nested
# builder as a child when it was opened in the same scope as its parent
# (``Builder.__enter__``'s ``same_scope`` check), so a sketch opened from a
# helper method would never reach the enclosing ``BuildPart``'s pending faces
# and the following ``extrude`` would fail. Helpers here are therefore pure:
# they take and return plain geometry and never touch the ambient builder.


def _pivot_dome(radius: float, depth: float) -> Part:
  """The elliptical dome a hinge turns on — ``radius`` across, ``depth`` proud."""
  with BuildPart() as dome:
    with BuildSketch(Plane.ZX):
      with BuildLine():
        arc = EllipticalCenterArc((0, 0), depth, radius)
        Polyline([arc @ 0, (0, 0), arc @ 1])
      make_face()
    revolve()
  return dome.part


def _pocket_plane(center: Vector, edge: Edge) -> Plane:
  """The plane a hinge pocket is cut on: sitting on ``edge``, turned 45° into the
  cube so the pocket straddles the edge evenly."""
  normal = Axis(edge)
  return Plane(
    origin=edge.center(),
    x_dir=(center - edge.center()).normalized().rotate(normal, -45),
    z_dir=normal.direction,
  )


def _pivot_planes(plane: Plane, params: Parameters) -> list[Plane]:
  """Where the two pivot domes sit inside a pocket, facing each other.

  The pocket is cut by extruding ``±connector_width/2`` along ``plane``'s normal,
  so its two walls are known planes — no need to fish the cut faces back out of
  the builder."""
  offset = params.connector_height / 2
  half_w = params.connector_width / 2
  origin = plane.origin + plane.x_dir * offset + plane.y_dir * offset
  n = plane.z_dir
  return [
    Plane(origin=origin + n * half_w, z_dir=-n),
    Plane(origin=origin - n * half_w, z_dir=n),
  ]


@memo
def _cube(params: Parameters, side: Literal["left", "right"]) -> Part:
  """One chamfered cube with two hinge pockets, each holding a pair of pivots.

  ``side`` picks which end of the top face gets the second pocket; the two
  handednesses are what let the eight cubes chain into a loop. Pocket placement
  reads off an unchamfered reference cube, so the chamfer never shifts an edge
  out from under a pocket."""
  s = params.cube_size
  h = params.connector_height
  ref = Box(s, s, s)
  bottom, top = ref.faces().filter_by(Axis.Z).sort_by(Axis.Z)
  top_edge = (top.edges() > Axis.X)[0] if side == "left" else (top.edges() < Axis.X)[0]
  bottom_edge = (bottom.edges() < Axis.Y)[0]
  dome = _pivot_dome(params.pivot_radius, params.pivot_depth)

  with BuildPart() as bp:
    Box(s, s, s)
    chamfer(bp.edges(), params.cube_chamfer)
    for edge in (top_edge, bottom_edge):
      plane = _pocket_plane(ref.center(), edge)
      with BuildSketch(plane):
        with BuildLine():
          # A rounded-off square, notched at two corners so the connector can
          # swing right through without the pocket mouth fouling it.
          Polyline(
            (0, 0),
            (h, 0),
            (h + h / 8, h / 8),
            (h + h / 8, h - h / 4),
            (h - h / 4, h + h / 8),
            (h / 8, h + h / 8),
            (0, h),
            close=True,
          )
        make_face()
      extrude(amount=params.connector_width / 2, both=True, mode=Mode.SUBTRACT)
      with Locations(*_pivot_planes(plane, params)):
        insert(dome)
  return bp.part


@memo
def _connector(params: Parameters) -> Part:
  """The hinge: a flat link with a snap socket at each end.

  Only a quarter is drawn — one socket, one side — then mirrored across XZ for
  the full width and across YZ for the far socket. Each socket is the seated
  pocket (pivot + clearance) with a narrower mouth lofted onto it, and that
  step is what the pivot has to push past to click in."""
  hh = params.connector_height / 2
  cw = params.connector_width

  pivot = _pivot_dome(
    params.pivot_radius + params.pivot_tolerance,
    params.pivot_depth + params.pivot_tolerance,
  )
  pivot_section = section(pivot, section_by=Plane.YZ, mode=Mode.PRIVATE)
  pivot_snap = _pivot_dome(
    params.pivot_radius - params.connector_snap_tolerance,
    params.pivot_depth - params.connector_snap_tolerance,
  )
  pivot_snap_section = section(pivot_snap, section_by=Plane.YZ, mode=Mode.PRIVATE)

  with BuildPart() as bp:
    with BuildSketch(Plane.XZ):
      with BuildLine():
        w = hh + hh + params.cube_spacing / 2 - params.connector_tolerance
        body = Polyline(
          (0, 0),
          (0, hh),
          (w - hh / 2 - params.connector_tolerance, hh),
        )
        # A 45° cut back into a tangent arc: the nose has to clear the pocket
        # mouth of the cube it swings into.
        nose = PolarLine(body @ 1, hh / 2, -45)
        TangentArc(((w, 0), (nose @ 1)), tangent=(0, 1))
        mirror(about=Plane.XZ)
      make_face()
    extrude(amount=cw / 2 - params.connector_side_tolerance)

    pivot_loc = -bp.faces().sort_by(Axis.Y).first.location
    pivot_loc.position += Vector(hh + params.cube_spacing, 0, 0)
    with Locations(pivot_loc):
      insert(pivot, mode=Mode.SUBTRACT)
    sections = (
      pivot_snap_section.located(pivot_loc),
      pivot_snap_section.located(pivot_loc).move(Pos(params.pivot_depth, 0, 0)),
      pivot_section.located(pivot_loc).move(Pos(hh, 0, 0)),
    )
    loft(sections, ruled=True, mode=Mode.SUBTRACT)
    mirror(about=Plane.XZ)
    mirror(about=Plane.YZ)
  return bp.part


# --------------------------------------------------------------------------- #
# Layouts — where each of the sixteen pieces goes
# --------------------------------------------------------------------------- #
#
# Both arrangements are computed for every build: the viewer shows one, and the
# 3MF export needs the other to place each object on the bed. They are pure
# ``Location`` tables over the same three solids, so the second one is free.

# Which handedness sits where, and how it is turned, in the assembled loop.
_ASSEMBLED_CUBES: list[tuple[str, tuple[int, int], tuple[float, float, float]]] = [
  ("right", (0, 0), (0, 0, -90)),
  ("left", (0, -1), (0, 0, -90)),
  ("left", (1, 0), (-90, 90, 0)),
  ("right", (1, -1), (-90, 90, 0)),
  ("right", (2, 0), (-90, -90, 0)),
  ("left", (2, -1), (-90, -90, 0)),
  ("left", (3, 0), (0, 0, 90)),
  ("right", (3, -1), (0, 0, 90)),
]


def _assembled_cube_locations(params: Parameters) -> list[tuple[str, Location]]:
  """The eight cubes in the folded loop: a 4x2 block, each cube turned so its
  two pockets face the neighbours it hinges to."""
  pitch = params.cube_size + params.cube_spacing
  return [
    (side, Location(Vector(x * pitch, y * pitch, 0)) * Rotation(*rot))
    for side, (x, y), rot in _ASSEMBLED_CUBES
  ]


def _assembled_connector_locations(params: Parameters) -> list[Location]:
  """The eight hinges, seated in the pockets of the cubes they join."""
  s = params.cube_size
  cs = params.cube_spacing
  hcs = cs / 2
  hch = params.connector_height / 2
  table: list[tuple[tuple[float, float, float], tuple[float, float, float]]] = [
    # Top pair
    ((0, 0, 90), (0, -s / 2 - hcs, s / 2 - hch)),
    ((0, 0, 90), ((s + cs) * 3, -s / 2 - hcs, s / 2 - hch)),
    # Bottom four
    ((0, 0, 0), (s / 2 + hcs, 0, -s / 2 + hch)),
    ((0, 0, 0), (s / 2 + hcs, -s - cs, -s / 2 + hch)),
    ((0, 0, 0), (s / 2 + hcs + (s + cs) * 2, 0, -s / 2 + hch)),
    ((0, 0, 0), (s / 2 + hcs + (s + cs) * 2, -s - cs, -s / 2 + hch)),
    # Side pair
    ((90, 0, 0), ((s / 2) + hcs + (s + cs), s / 2 - hch, 0)),
    ((90, 0, 0), ((s / 2) + hcs + (s + cs), -s / 2 - (s + cs) + hch, 0)),
  ]
  return [Location(Vector(*pos)) * Rotation(*rot) for rot, pos in table]


# Room left between pieces on the bed. `cube_spacing` is a *mechanism* clearance —
# how freely a hinge turns — and at its default of 0.1 mm it would leave neighbours
# 0.2 mm apart, far too close for a slicer to treat them as separate prints. The
# print layout therefore never goes below this.
_MIN_BED_GAP = 2.0


def _bed_gap(params: Parameters) -> float:
  """Gap between neighbouring pieces in the print layout."""
  return max(params.cube_spacing * 2, _MIN_BED_GAP)


def _flat_cube_locations(params: Parameters) -> list[tuple[str, Location]]:
  """The eight cubes as a plain 4x2 grid, upright and spaced for slicing."""
  pitch = params.cube_size + _bed_gap(params)
  return [
    (
      "right" if (x + y) % 2 == 0 else "left",
      Location(Vector(x * pitch, y * pitch, 0)),
    )
    for x, y in itertools.product(range(4), range(2))
  ]


def _flat_connector_locations(params: Parameters, connector: Part) -> list[Location]:
  """The eight hinges as their own 4x2 grid, pitched off the connector's bounds."""
  size = connector.bounding_box(optimal=False).size
  gap = _bed_gap(params)
  return [
    Location(loc.position)
    for loc in GridLocations(size.X + gap, size.Y + gap, 4, 2)
  ]


def _bounds(placed: list[Part]) -> BoundBox:
  """Combined bounding box of already-placed pieces."""
  box = placed[0].bounding_box(optimal=False)
  for shape in placed[1:]:
    box = box.add(shape.bounding_box(optimal=False))
  return box


def _shift(box: BoundBox, center: Vector) -> Location:
  """The move that centres ``box`` on ``center`` in XY and drops it onto z=0."""
  return Location(
    Vector(center.X - box.center().X, center.Y - box.center().Y, -box.min.Z)
  )


# --------------------------------------------------------------------------- #
# The design
# --------------------------------------------------------------------------- #


# The two piece kinds read apart at a glance, and match the filaments the export
# assigns them to.
_CUBE_COLOR = Color(0.88, 0.29, 0.29)
_CONNECTOR_COLOR = Color(0.20, 0.20, 0.22)

# Interpenetration past which a hinge is judged to bind rather than merely touch.
_HINGE_BIND_MM3 = 0.01


class InfinityCube(Design[Parameters]):
  """Eight cubes and eight snap-on hinges. Instantiating with a ``Parameters``
  builds the chosen arrangement into ``self`` (a ``Compound``), while the other
  arrangement is kept as ``Location``s so the export can lay the pieces flat."""

  PRESET_LABEL: ClassVar[str] = "Size"
  # Sixteen placed copies of three solids, bounded and re-bounded while laying
  # them out; under the patch (no revalidation on `moved`, cheap copies) the build
  # drops from ~0.9 s to ~0.6 s natively. Every box here already asks for the
  # precision it needs.
  FAST_PATCH: ClassVar[bool] = True
  PRESETS: ClassVar[list[Preset[Parameters]]] = [
    Preset(
      name="Tiny",
      description="5 mm cubes — a keyring-sized fidget, tight tolerances.",
      params=Parameters(
        cube_size=5.0,
        cube_chamfer=0.5,
        connector_height=1.5,
        connector_width_ratio=0.9,
        pivot_radius=0.5,
        pivot_depth=0.5,
        connector_tolerance=0.05,
        connector_snap_tolerance=0.1,
        pivot_tolerance=0.05,
      ),
    ),
    Preset(
      name="Standard",
      description="10 mm cubes — the everyday size, most dialled in.",
      params=Parameters(),
    ),
    Preset(
      name="Large",
      description="15 mm cubes — chunky in the hand, forgiving to print.",
      params=Parameters(
        cube_size=15.0,
        cube_chamfer=3.0,
        connector_height=5.5,
        connector_width_ratio=0.9,
        pivot_radius=1.75,
        pivot_depth=1.75,
        connector_tolerance=0.4,
        connector_snap_tolerance=0.3,
        pivot_tolerance=0.1,
      ),
    ),
    Preset(
      name="Jumbo",
      description="25 mm cubes — a desk toy, loose tolerances throughout.",
      params=Parameters(
        cube_size=25.0,
        cube_chamfer=3.0,
        connector_height=7.0,
        connector_width_ratio=0.9,
        pivot_radius=2.0,
        pivot_depth=2.0,
        connector_tolerance=0.4,
        connector_snap_tolerance=0.3,
        pivot_tolerance=0.1,
      ),
    ),
  ]

  def __init__(self, params: Parameters):
    self.params = params

    left = _cube(params, "left")
    right = _cube(params, "right")
    connector = _connector(params)
    bases = {"left": left, "right": right}

    # Both arrangements, as Location tables over those same three solids: the
    # viewer shows one, and the export needs the other to place each piece on the
    # bed. Only locations, so carrying both costs nothing.
    asm_sides, asm_cube_locs = (list(t) for t in zip(*_assembled_cube_locations(params)))
    asm_cube_bases = [bases[side] for side in asm_sides]
    asm_conn_locs = _assembled_connector_locations(params)
    # Assembled, the cubes and connectors are one rigid body, so they share a
    # single bedding shift rather than each being bedded on its own.
    asm_shift = _shift(
      _bounds(
        [loc * base for base, loc in zip(asm_cube_bases, asm_cube_locs)]
        + [loc * connector for loc in asm_conn_locs]
      ),
      Vector(0, 0, 0),
    )
    asm_cube_locs = [asm_shift * loc for loc in asm_cube_locs]
    asm_conn_locs = [asm_shift * loc for loc in asm_conn_locs]

    flat_sides, flat_cube_locs = (list(t) for t in zip(*_flat_cube_locations(params)))
    flat_cube_bases = [bases[side] for side in flat_sides]
    flat_conn_locs = _flat_connector_locations(params, connector)
    cube_box = _bounds(
      [loc * base for base, loc in zip(flat_cube_bases, flat_cube_locs)]
    )
    conn_box = _bounds([loc * connector for loc in flat_conn_locs])
    # Two blocks, cubes below the origin and connectors above, separated by
    # exactly `gap` and both centred in X. The blocks are different depths, so a
    # final nudge puts the plate as a whole on the origin.
    gap = _bed_gap(params)
    plate = Vector(0, (cube_box.size.Y - conn_box.size.Y) / 2, 0)
    cube_shift = _shift(cube_box, plate - Vector(0, (gap + cube_box.size.Y) / 2, 0))
    conn_shift = _shift(conn_box, plate + Vector(0, (gap + conn_box.size.Y) / 2, 0))
    flat_cube_locs = [cube_shift * loc for loc in flat_cube_locs]
    flat_conn_locs = [conn_shift * loc for loc in flat_conn_locs]

    use_flat = params.layout == "flat"
    cube_locs = flat_cube_locs if use_flat else asm_cube_locs
    conn_locs = flat_conn_locs if use_flat else asm_conn_locs
    cube_bases = flat_cube_bases if use_flat else asm_cube_bases

    # Kept for `analyze` and `export_objects`.
    self.cube_bases = cube_bases
    self.connector_base = connector
    self.cube_locations = cube_locs
    self.connector_locations = conn_locs
    self.flat_cube_locations = flat_cube_locs
    self.flat_connector_locations = flat_conn_locs
    # One of each kind in its print pose, for the bed-contact checks. The two cube
    # handednesses are mirror images, so either stands for both.
    self.printed_cube = flat_cube_locs[0] * flat_cube_bases[0]
    self.printed_connector = flat_conn_locs[0] * connector

    self.cubes = [
      self._placed(base, loc, f"Cube {i + 1}", _CUBE_COLOR)
      for i, (base, loc) in enumerate(zip(cube_bases, cube_locs))
    ]
    self.connectors = [
      self._placed(connector, loc, f"Connector {i + 1}", _CONNECTOR_COLOR)
      for i, loc in enumerate(conn_locs)
    ]

    super().__init__(
      None,
      label="infinity_cube",
      children=[
        Compound(label="Cubes", children=self.cubes),
        Compound(label="Connectors", children=self.connectors),
      ],
    )

  @staticmethod
  def _placed(base: Part, loc: Location, label: str, color: Color) -> Part:
    piece = loc * base
    piece.label = label
    piece.color = color
    return piece

  def export_objects(self) -> list[ExportObject]:
    """One slicer object per piece — sixteen of them.

    The pieces are printed loose and snapped together, so each has to stay
    separately selectable: cubes on filament 1, connectors on filament 2, and any
    of them deletable on its own. Per-object granularity is also what lets each
    piece carry its own ``print_location``, so the assembled arrangement can be
    exported and still land flat on the bed."""
    objects: list[ExportObject] = []
    for piece, design_loc, flat_loc in zip(
      self.cubes, self.cube_locations, self.flat_cube_locations
    ):
      objects.append(
        ExportObject(
          name=piece.label,
          shape=piece,
          print_location=flat_loc * design_loc.inverse(),
          extruder=1,
        )
      )
    for piece, design_loc, flat_loc in zip(
      self.connectors, self.connector_locations, self.flat_connector_locations
    ):
      objects.append(
        ExportObject(
          name=piece.label,
          shape=piece,
          print_location=flat_loc * design_loc.inverse(),
          extruder=2,
        )
      )
    return objects

  def analyze(self) -> Report:
    """Assess the pieces' printability and the hinge fit.

    The generic checks come from ``Report``; what is design-specific is that this
    is an *assembly* — the two things that decide whether it works are the snap
    interference and the seated clearance, neither of which a print-in-place
    check would look at."""
    report = Report()

    # 0. Degenerate geometry. Every piece is a rigid-transform copy of one of
    # three solids, so one cube and one connector stand for all sixteen — the
    # full BRep validity check is far too costly to repeat per piece.
    if not report.check_geometry(self, sample=[self.cubes[0], self.connectors[0]]):
      return report

    flat = self.params.layout == "flat"

    # 1. Bed contact, per piece kind. Both are measured on the piece in its
    # *print* pose, not the pose on screen: what a piece stands on is a property
    # of how it prints, and in the assembled view the hinges are turned on their
    # sides. The connector is the one to watch — it is much the smaller piece, and
    # it lands on a thin strip of its flank.
    # The bands already own the brim/raft advice, so these name the levers in the
    # form instead — the parameter to reach for, not the slicer setting.
    report.check_bed_contact(
      self.printed_cube.faces().sort_by(Axis.Z)[0].area,
      label="Bed contact per cube",
      suggestion="A smaller chamfer leaves a bigger flat to stand on.",
    )
    report.check_bed_contact(
      self.printed_connector.faces().sort_by(Axis.Z)[0].area,
      label="Bed contact per connector",
      suggestion="A wider or taller connector gives it more to stand on.",
    )

    # 2. Snap interference — the parameter these presets were built around. The
    # socket mouth is undersized by `connector_snap_tolerance`; the pivot has to
    # deform past it and then stay put.
    self._check_snap(report)

    if flat:
      # 3. Floating sections — every piece is bedded by construction, so this is a
      # cheap ok-guard that would catch a regression lifting one off the bed.
      report.check_floating(self)
    else:
      # 3. Does the hinge bind? `check_overlap` is the print-in-place question
      # ("will these fuse in the print?") and does not apply here — these pieces
      # are printed apart and pushed together, so *touching* is the point. What
      # would be wrong is interpenetration: solid where two pieces both want to
      # be, meaning the hinge cannot close. Connector 1 seats into cube 1 by
      # construction, so one pair stands for all eight.
      self._check_hinge(report)
      report.add(
        "Assembled preview",
        16,
        detail="Showing the finished toy — pieces sit where they end up, not "
        "where they print. Exporting still lays all sixteen flat on the bed.",
        status="ok",
      )

    return report

  def _check_hinge(self, report: Report) -> None:
    """Interpenetration between a hinge and a cube it seats into, which is what
    tells you the pocket and socket actually accept each other at these
    tolerances."""
    overlap = (self.cubes[0] & self.connectors[0]).volume
    binds = overlap > _HINGE_BIND_MM3
    report.add(
      "Hinge interference",
      round(overlap, 3),
      unit="mm³",
      detail="Hinge binds — the connector and cube want the same space, so it "
      "will not seat. Loosen the connector or pivot fit."
      if binds
      else "Connector seats into the pocket without binding.",
      status="error" if binds else "ok",
    )

  def _check_snap(self, report: Report) -> None:
    """Judge the snap fit: interference as a fraction of the pivot it swallows.

    Too little and the connector rattles off in use; too much and it splits going
    on, since the socket mouth has to stretch by that much. The bands are read off
    the sizes that print well in practice."""
    params = self.params
    pivot = min(params.pivot_radius, params.pivot_depth)
    ratio = params.connector_snap_tolerance / pivot

    if params.connector_snap_tolerance < 0.05:
      status: Status = "warning"
      detail = "Almost no interference — the hinges will fall off. Raise it."
    elif ratio > 0.45:
      status = "warning"
      detail = (
        "Very tight — the connector has to stretch a long way to go on and may "
        "split. Lower it, or use a larger pivot."
      )
    elif ratio < 0.12:
      status = "warning"
      detail = "Loose — the hinges may work themselves off. Raise it a little."
    else:
      status = "ok"
      detail = "Should click on and stay on."
    report.add(
      "Snap interference",
      round(ratio * 100, 1),
      unit="% of pivot",
      detail=detail,
      status=status,
    )
