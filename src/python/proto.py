"""The design-authoring SDK: the seam a parametric design plugs into.

This module is the stable framework side of the contract (it is written to the
Pyodide FS as an *importable* module, and sits next to a ``design.py`` on
``sys.path`` natively). A design — a bundled ``design.py`` — imports from here and
exposes exactly one ``Design`` subclass. Nothing design-specific lives here.

The contract, in one place:

* The module **docstring** is the design's gallery copy: first line = its name,
  the rest = its description. ``__author__`` and ``__urls__`` (``{label: url}``,
  rendered as footer links) are optional, as is ``__blank__ = True`` for the
  "start from scratch" scaffold. These are scraped from the source text at build
  time (see ``src/designs/manifest.ts``) — no Python boot needed.
* ``class Parameters(BaseModel)`` — the form/validation source of truth. Use the
  field helpers below; a field's **attribute docstring** (the string literal
  under it) is its description. Cross-field rules go in a
  ``@model_validator(mode="after")``, never duplicated in JS.
* ``class MyDesign(Design[Parameters])`` — instantiating ``MyDesign(params)``
  *builds* the geometry into ``self`` (a build123d ``Compound``), ready to
  tessellate / export.
* ``MyDesign.analyze(self) -> Report`` — optional; the analysis report.
  ``Report``'s ``check_*`` methods carry the generic FDM knowledge.
* ``MyDesign.export_objects(self) -> list[ExportObject] | None`` — optional; how
  the design splits into slicer objects on 3MF export. Default (``None``) is one
  object for the whole design, right for a print-in-place model; an assembly
  returns one ``ExportObject`` per separable piece.
* ``MyDesign.PRESETS: list[Preset[Parameters]]`` — optional; curated starting
  points, each holding a real (validated) ``Parameters``. ``PRESET_LABEL`` names
  what they vary, in the user's words ("Link shape").

Field helpers (``SliderField`` / ``ChoiceField`` / ``SelectField`` /
``CardsField`` / ``ToggleField``) wrap pydantic's ``Field``: they take the
validation bounds *and* the widget hints, and stamp a ``widget`` discriminator
(``"slider"`` / ``"shape"`` / ``"select"`` / ``"cards"`` / ``"toggle"``) into
``json_schema_extra`` that the
frontend form switches on (see ``src/components/ParamForm.vue``). Which helper
you call is what picks the widget. Add a new parameter type by adding a helper
here and a matching branch there. They share a set of display hints — ``size``,
``show_if``, ``preset_stable``, ``recenter``, ``advanced`` — documented above the
helpers themselves.

Help text (field docstrings, ``Choice`` member and ``Option`` descriptions,
preset descriptions) is written hard-wrapped like any docstring; it is *unwrapped*
on the way out (lines within a paragraph joined, blank-line paragraph breaks and
``-``/``*`` list items kept), so the form can show it with its line breaks intact
without also showing where the source happened to wrap.

Speed, both opt-in and independent of each other:

* ``MyDesign.FAST_PATCH = True`` runs the design's build and analysis under
  ``build123d_utils_jas.apply_fast_patch()`` (scoped per call by
  ``fast_patch``, so a design that doesn't opt in keeps stock build123d). It
  swaps a few ``Shape`` methods for cheaper ones: ``copy.copy`` shares the
  underlying ``TopoDS_Shape`` instead of deep-copying it, ``moved`` / ``located``
  skip revalidation, and ``bounding_box()`` defaults to the coarse (non-optimal)
  box. A design that enables it passes ``optimal=True`` wherever a bounding box
  feeds a threshold.
* ``@memo`` caches a sub-part builder ``f(params, *args)`` *across* builds, keyed
  on the fields it actually read — see ``memo``.
"""

from __future__ import annotations

import contextlib
import copy
import functools
import inspect
import io
import re
import types
from collections import OrderedDict
from dataclasses import dataclass
from typing import (
  TYPE_CHECKING,
  Any,
  Callable,
  ClassVar,
  Generic,
  Iterator,
  Literal,
  Mapping,
  ParamSpec,
  Sequence,
  TypeVar,
  cast,
  get_args,
  get_origin,
)

import pydantic
from build123d import Compound, ExportSVG, Location, Wire
from build123d.topology.shape_core import Shape
from ocp_tessellate.ocp_utils import dist_shapes
from pydantic import ConfigDict, Field, ValidationError, model_validator

__all__ = [
  "BaseModel",
  "Choice",
  "member",
  "Option",
  "outline_svg",
  "SliderField",
  "ChoiceField",
  "SelectField",
  "CardsField",
  "ToggleField",
  "FIRST_LAYER_HEIGHT",
  "MAX_OVERHANG_DEG",
  "Status",
  "Report",
  "Preset",
  "ExportObject",
  "ExportPart",
  "Design",
  "load_design",
  "fast_patch",
  "memo",
  "clear_memo",
  "memo_stats",
  "Field",
  "ValidationError",
  "model_validator",
]

T = TypeVar("T")
C = TypeVar("C", bound="Choice[Any]")
P = TypeVar("P", bound="BaseModel")
PS = ParamSpec("PS")


_LIST_ITEM = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s")


def _unwrap(text: str) -> str:
  """Undo a docstring's hard wrapping: lines within a paragraph are joined with a
  space, while blank-line paragraph breaks — and lines that start a list item —
  are kept. Help text is shown with its line breaks intact, so without this the
  reader would see every place the source wrapped at 80 columns."""
  paragraphs = []
  for para in re.split(r"\n\s*\n", inspect.cleandoc(text)):
    lines: list[str] = []
    for line in para.splitlines():
      stripped = line.strip()
      if lines and not _LIST_ITEM.match(line):
        lines[-1] = f"{lines[-1]} {stripped}"
      else:
        lines.append(stripped)
    paragraphs.append("\n".join(lines))
  return "\n\n".join(p for p in paragraphs if p)


def _unwrap_descriptions(node: Any) -> None:
  """Unwrap every ``description`` string in a JSON schema, in place — the
  fields' own and those inside widget hints (options) alike."""
  if isinstance(node, dict):
    for key, value in node.items():
      if key == "description" and isinstance(value, str):
        node[key] = _unwrap(value)
      else:
        _unwrap_descriptions(value)
  elif isinstance(node, list):
    for item in node:
      _unwrap_descriptions(item)


class BaseModel(pydantic.BaseModel):
  """Base for a design's ``Parameters``. Identical to pydantic's, except that a
  field's **attribute docstring** becomes its schema description — so the help
  text sits under the field it describes instead of inside its constructor —
  and descriptions come out of ``model_json_schema`` unwrapped (see ``_unwrap``)."""

  model_config = ConfigDict(use_attribute_docstrings=True)

  @classmethod
  def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
    schema = super().model_json_schema(*args, **kwargs)
    _unwrap_descriptions(schema)
    return schema


# --------------------------------------------------------------------------- #
# Choice — an enum whose members are geometry builders.
# --------------------------------------------------------------------------- #


class member(Generic[T]):
  """Declares one ``Choice`` member — wrap the builder's ``@staticmethod`` with it.

  ``ChoiceMeta`` swaps this wrapper for the member itself at class creation, so at
  runtime it is inert. Its job is static: a bare ``@staticmethod`` reads to a type
  checker as the plain function, which is *also* true (a member is callable with
  the builder's signature), but then ``MyChoice.thing`` can't be passed where a
  ``MyChoice`` is expected — a preset's ``Parameters(...)``, say. The descriptor
  below says both: on the class it is a ``MyChoice``, and ``Choice.__call__``
  keeps the builder's signature."""

  def __init__(self, fn: T) -> None:
    self.fn = fn

  if TYPE_CHECKING:

    def __get__(self, obj: object, owner: type[C]) -> C: ...


class ChoiceMeta(type):
  _members_: dict[str, Any]

  def __new__(mcls, name, bases, ns, **kw):
    cls = super().__new__(mcls, name, bases, ns, **kw)
    cls._members_ = {}
    for key, val in list(ns.items()):
      if key.startswith("_") or not isinstance(val, member):
        continue
      fn = val.fn
      if isinstance(fn, staticmethod):
        fn = fn.__func__
      m = object.__new__(cls)
      doc = inspect.getdoc(fn) or ""
      first, _, rest = doc.partition("\n")
      object.__setattr__(m, "_impl", fn)
      object.__setattr__(m, "name", key)
      object.__setattr__(m, "value", key)
      object.__setattr__(m, "label", first.strip() or key)
      object.__setattr__(m, "description", _unwrap(rest) or None)
      setattr(cls, key, m)
      cls._members_[key] = m
    return cls

  def __iter__(cls: type[C]) -> Iterator[C]:  # pyright: ignore[reportGeneralTypeIssues]
    return iter(cls._members_.values())

  def __len__(cls) -> int:
    return len(cls._members_)

  def __contains__(cls, key: object) -> bool:
    return key in cls._members_


class Choice(Generic[T], metaclass=ChoiceMeta):
  """A lightweight enum whose members are ``@member @staticmethod`` builders: the
  docstring's first line is the member's label and the rest its description, and
  calling a member runs the builder. A ``Choice`` is therefore both the form's
  option list and the geometry behind it — see ``ChoiceField``, whose previews are
  generated from the real build123d output so they can't drift from what gets
  built. The generic argument is the builders' shared signature."""

  name: str
  value: str
  label: str
  description: str | None
  _impl: T

  if TYPE_CHECKING:
    # To the checker, calling a member has the signature of T
    # (e.g. Callable[[float, float], Wire]).
    __call__: T
  else:

    def __call__(self, *args, **kwargs):
      return self._impl(*args, **kwargs)

  def __repr__(self) -> str:
    return f"<{type(self).__name__}.{getattr(self, 'name', '?')}>"

  @classmethod
  def __get_pydantic_core_schema__(cls, source, handler):
    from pydantic_core import core_schema

    def validate(v):
      if isinstance(v, cls):
        return v
      try:
        return cls._members_[v]
      except KeyError:
        raise ValueError(f"{v!r} is not a valid {cls.__name__}")

    return core_schema.no_info_plain_validator_function(
      validate,
      serialization=core_schema.plain_serializer_function_ser_schema(
        lambda m: m.value, return_schema=core_schema.str_schema()
      ),
    )

  @classmethod
  def __get_pydantic_json_schema__(cls, schema, handler):
    out = {
      "type": "string",
      "enum": [m.value for m in cls._members_.values()],
      "options": [
        {
          "value": m.value,
          "label": m.label,
          **({"description": m.description} if m.description else {}),
        }
        for m in cls._members_.values()
      ],
    }
    return out


def outline_svg(outline: Shape | Sequence[Shape]) -> dict:
  """Export a 2D outline to a minimal SVG, captured as ``{viewBox, paths}`` (CAD
  coords; the frontend flips Y to match SVG's orientation). The frontend renders
  these directly, so previews come straight from the real geometry and can never
  drift from what a build produces.

  Usually one ``Wire``, but any 2D shape — or several, for a preview of pieces
  that don't touch (a print layout, say) — since they are only ever drawn.

  ExportSVG pulls in ezdxf only for colour handling; the Pyodide worker mocks
  ezdxf but gives ``ezdxf.colors`` a real ``RGB``/``aci2rgb`` so this path works
  there too (see ``install.py``)."""
  exporter = ExportSVG()
  exporter.add_shape(outline)
  bio = io.BytesIO()
  exporter.write(bio)
  svg = bio.getvalue().decode()
  view_box = re.search(r'viewBox="([^"]*)"', svg)
  return {
    "viewBox": view_box.group(1) if view_box else "",
    "paths": re.findall(r'\sd="([^"]*)"', svg),
  }


# --------------------------------------------------------------------------- #
# Field helpers — a pydantic Field plus the hints that render it.
# --------------------------------------------------------------------------- #
#
# Each returns a plain ``Field(...)``, typed ``Any`` (as pydantic's own ``Field``
# is) so a field can be assigned its natural type without a cast. The widget
# hints ride along in ``json_schema_extra``; ``name`` is the form label, and the
# field's *attribute docstring* is its description. Options common to all:
# ``size`` (a Nuxt UI token), ``show_if`` (conditional visibility: a map of
# other-field -> required value(s)), ``preset_stable`` (this field belongs to
# the user, not to a preset — see ``Design.PRESETS``), ``recenter`` (changing
# it reframes the 3D view instead of holding the current camera — for the few
# parameters that rearrange the whole design rather than tweak it), and
# ``advanced`` (the form files it under a closed "Advanced" disclosure at the
# end, rather than in the main run of controls — for the fit-and-finish numbers
# a design has to expose but most people never touch).


def _hints(widget: str, name: str, **hints: Any) -> dict:
  """A ``json_schema_extra`` dict: the widget discriminator, the label, and
  whichever hints were actually given (unset ones are left out entirely)."""
  extra: dict[str, Any] = {"widget": widget, "label": name}
  for key, value in hints.items():
    if value is not None and value is not False:
      extra[key] = value
  return extra


@dataclass(frozen=True)
class Option:
  """One choice of a ``SelectField`` / ``CardsField``. ``wire`` is an optional
  build123d outline rendered as the option's preview — both widgets show it when
  present (the dropdown small, the cards large) and both work without it. It takes
  several shapes as well as one, for an option that pictures loose pieces."""

  value: str | int
  label: str
  description: str | None = None
  wire: Wire | Shape | Sequence[Shape] | None = None

  def to_dict(self) -> dict:
    out: dict[str, Any] = {"value": self.value, "label": self.label}
    if self.description:
      out["description"] = _unwrap(self.description)
    if self.wire is not None:
      out["svg"] = outline_svg(self.wire)
    return out


def SliderField(
  *,
  default: float,
  name: str,
  step: float,
  ge: float | None = None,
  gt: float | None = None,
  le: float | None = None,
  lt: float | None = None,
  slider_min: float | None = None,
  slider_max: float | None = None,
  unit: str | None = None,
  size: str | None = None,
  show_if: Mapping[str, Any] | None = None,
  # Mappings (not dicts) throughout: a `dict` is invariant in its value type, so a
  # literal `{"decagon": 2}` wouldn't fit a `dict[str, float]` parameter.
  slider_max_by: Mapping[str, Mapping[str, float]] | None = None,
  preset_stable: bool = False,
  recenter: bool = False,
  advanced: bool = False,
) -> Any:
  """A numeric slider + number input.

  ``ge``/``gt``/``le``/``lt`` are the hard validation bounds. ``slider_min`` /
  ``slider_max`` narrow the *slider* to a comfortable range (a value outside it
  can still be typed into the number input); they default to the hard bounds, so
  a parameter whose comfortable range is its whole range says it once.
  ``slider_max_by`` (``{field: {value: max}}``) makes the max depend on another
  field's current value."""
  low = slider_min if slider_min is not None else ge if ge is not None else gt
  high = slider_max if slider_max is not None else le if le is not None else lt
  if low is None or high is None:
    raise ValueError(
      f"{name}: needs a slider range — give slider_min/slider_max or ge/gt and le/lt"
    )
  return Field(
    default=default,
    ge=ge,
    gt=gt,
    le=le,
    lt=lt,
    json_schema_extra=_hints(
      "slider",
      name,
      sliderMin=low,
      sliderMax=high,
      step=step,
      unit=unit,
      size=size,
      show_if=show_if,
      slider_max_by=slider_max_by,
      preset_stable=preset_stable,
      recenter=recenter,
      advanced=advanced,
    ),
  )


def ChoiceField(
  *,
  default: Any,
  name: str,
  choices: type[C],
  preview: Callable[[C], Any],
  size: str | None = None,
  show_if: Mapping[str, Any] | None = None,
  preset_stable: bool = False,
  recenter: bool = False,
  advanced: bool = False,
) -> Any:
  """A picker over a ``Choice`` type. ``preview(member)`` builds the outline each
  option is drawn as, at an icon-sized scale — anything with a ``.wire()`` (a
  build123d 2D object) or a ``Wire`` itself."""
  return Field(
    default=default,
    json_schema_extra=_hints(
      "shape",
      name,
      options=[
        {
          "value": m.value,
          "label": m.label,
          **({"description": m.description} if m.description else {}),
          "svg": outline_svg(_as_wire(preview(m))),
        }
        for m in choices
      ],
      size=size,
      show_if=show_if,
      preset_stable=preset_stable,
      recenter=recenter,
      advanced=advanced,
    ),
  )


def SelectField(
  *,
  default: Any,
  name: str,
  choices: list[Option],
  size: str | None = None,
  show_if: Mapping[str, Any] | None = None,
  preset_stable: bool = False,
  recenter: bool = False,
  advanced: bool = False,
) -> Any:
  """A plain dropdown over ``choices`` — for ``Literal``/enum fields (e.g. a
  brim style) whose options are values rather than geometry builders."""
  return Field(
    default=default,
    json_schema_extra=_hints(
      "select",
      name,
      options=[c.to_dict() for c in choices],
      size=size,
      show_if=show_if,
      preset_stable=preset_stable,
      recenter=recenter,
      advanced=advanced,
    ),
  )


def CardsField(
  *,
  default: Any,
  name: str,
  choices: list[Option],
  size: str | None = None,
  show_if: Mapping[str, Any] | None = None,
  preset_stable: bool = False,
  recenter: bool = False,
  advanced: bool = False,
) -> Any:
  """The same options as ``SelectField``, laid out as big side-by-side tiles
  instead of a dropdown — for a prominent mode toggle where the choice deserves
  visual weight. Best kept to two or three options so they fit in a row."""
  return Field(
    default=default,
    json_schema_extra=_hints(
      "cards",
      name,
      options=[c.to_dict() for c in choices],
      size=size,
      show_if=show_if,
      preset_stable=preset_stable,
      recenter=recenter,
      advanced=advanced,
    ),
  )


def ToggleField(
  *,
  default: bool,
  name: str,
  size: str | None = None,
  show_if: Mapping[str, Any] | None = None,
  preset_stable: bool = False,
  recenter: bool = False,
  advanced: bool = False,
) -> Any:
  """An on/off switch for a ``bool`` field — for a feature a design either adds
  or doesn't (break-away supports, a chamfer) where two named options would be
  ceremony."""
  return Field(
    default=default,
    json_schema_extra=_hints(
      "toggle",
      name,
      size=size,
      show_if=show_if,
      preset_stable=preset_stable,
      recenter=recenter,
      advanced=advanced,
    ),
  )


def _as_wire(shape: Any) -> Wire:
  """A preview's outline: a ``Wire`` as-is, else whatever ``.wire()`` gives."""
  return shape if isinstance(shape, Wire) else shape.wire()


# --------------------------------------------------------------------------- #
# Report — a flat list of findings about the generated shape.
# --------------------------------------------------------------------------- #
#
# Canonical definition of the report shape; ``protocol.ts``'s
# ``PrintabilityReport`` mirrors ``to_dict()``. Build one imperatively while
# walking the geometry; ``overall_status`` / ``summary`` roll up automatically.
#
# The ``check_*`` methods encapsulate the common FDM knowledge
# (bed adhesion by contact area, part fusion vs. clearance, floating islands) so
# a design's ``analyze`` only has to pick *which* geometry to feed in.
# Thresholds/labels default to sensible values and are overridable per call.
# Nothing here is design-specific.

Status = Literal["ok", "warning", "error"]

# Printing-domain constants a design can reuse for geometry it sizes to the
# process rather than to its own parameters.
FIRST_LAYER_HEIGHT = 0.2  # mm, assumed slicer first-layer height
MAX_OVERHANG_DEG = 45.0  # deg from vertical past which a wall needs support

_STATUS_RANK: dict[str, int] = {"ok": 0, "warning": 1, "error": 2}
# The one-line verdict. Deliberately *not* a printability claim: what this can
# actually see is the shape it just generated, and how a given printer, filament
# and slicer will get on with it is not knowable from here. So `ok` says the
# shape came out clean, and anything else points at the findings rather than
# pronouncing on whether it will print.
_DEFAULT_SUMMARY: dict[str, str] = {
  "ok": "Looks good.",
  "warning": "Looks good — a few things worth checking below.",
  "error": "The generated shape has problems — see below.",
}

# Per-part effective contact footprint (mm²), ordered high -> low.
# band -> (min_area_mm2, item_status); below the smallest min -> _WORST_CONTACT.
DEFAULT_CONTACT_BANDS: dict[str, tuple[float, Status]] = {
  "easy": (60.0, "ok"),
  "medium": (20.0, "ok"),
  "hard": (15.0, "warning"),
  "expert": (10.0, "warning"),
}
# Below the smallest band. A warning, not an error: too little bed contact is a
# statement about adhesion — a brim, a raft or a different surface can rescue it —
# not about the shape being wrong. `error` is reserved for geometry that is
# actually broken (see `check_geometry` / `check_overlap`), which is also why this
# band is named for how hard it is rather than declared impossible.
_WORST_CONTACT: tuple[str, Status] = ("extreme", "warning")

_CONTACT_DETAIL: dict[str, str] = {
  "easy": "Plenty of bed contact.",
  "medium": "Should adhere on most beds.",
  "hard": "Small patch; adhesion may be marginal.",
  "expert": "Very little contact; likely to lift.",
  "extreme": "Barely any contact — expect to need a brim or raft.",
}
_MARGINAL_BANDS = frozenset({"hard", "expert", "extreme"})

_CLEARANCE_WARNING = 0.2  # mm, gap below which separate parts may still fuse
_OVERLAP_TOL = 1e-6  # mm, min distance below which parts are treated as touching
# Interpenetration volume (mm³) at/above which parts are clearly fused (error);
# any smaller overlap still interpenetrates but is flagged as a warning.
_OVERLAP_ERROR_MM3 = 5.0

_FLOATING_TOL = 1e-3  # mm, height above the bed plane a solid may sit and still count
_MIN_SOLID_VOLUME = 1e-6  # mm³, below which a solid is a degenerate sliver


def _classify_contact(
  area: float, bands: dict[str, tuple[float, Status]]
) -> tuple[str, Status]:
  """Map a contact footprint area (mm²) to a (difficulty band, status)."""
  for band, (min_area, status) in bands.items():
    if area >= min_area:
      return band, status
  return _WORST_CONTACT


class Report:
  """An analysis report: a flat list of findings plus an auto-derived overall
  status and summary. Pass ``summary`` to override the default one-liner; pass
  ``status`` to force the overall (used by ``Report.error``)."""

  def __init__(self, summary: str | None = None, status: Status | None = None):
    self._items: list[dict] = []
    self._summary = summary
    self._status = status

  def add(
    self,
    label: str,
    value: float,
    *,
    unit: str | None = None,
    detail: str | None = None,
    status: Status | None = None,
  ) -> "Report":
    """Append one finding (a labelled value with optional unit/detail/status).
    Returns ``self`` so calls can chain."""
    item: dict = {"label": label, "value": value}
    if unit is not None:
      item["unit"] = unit
    if detail is not None:
      item["detail"] = detail
    if status is not None:
      item["status"] = status
    self._items.append(item)
    return self

  # --- Generic checks ------------------------------------------------------ #

  def check_geometry(
    self,
    shape: Compound,
    *,
    label: str = "Geometry",
    min_volume: float = _MIN_SOLID_VOLUME,
    sample: Sequence[Shape] | None = None,
  ) -> bool:
    """Guard against degenerate build output before other checks run: an empty
    build (no solids), zero/near-zero-volume slivers, or an invalid/non-manifold
    BREP. Value = count of degenerate solids among those checked (0 -> ok).
    Returns ``True`` when the geometry is sound, so a caller can skip
    measurements that would be meaningless on broken geometry.

    ``is_valid`` is a full BRep check (~10ms/solid) and can dominate analyze
    time when ``shape`` has many solids. When ``sample`` is given, only its
    solids go through the expensive validity/volume check — safe when the rest
    are rigid-transform copies of what's sampled, since translation/rotation
    preserve both validity and volume; a design with repeated geometry (e.g. an
    array of identical parts) can judge that from its own construction. The
    reported solid count always reflects the full ``shape``."""
    solids = shape.solids()
    if not solids:
      self.add(
        label,
        0,
        detail="No solid geometry was produced — the build is empty.",
        status="error",
      )
      return False
    checked = solids if sample is None else [s for shp in sample for s in shp.solids()]
    degenerate = 0
    for s in checked:
      try:
        ok = s.is_valid and s.volume >= min_volume
      except Exception:
        ok = False
      if not ok:
        degenerate += 1
    if degenerate:
      self.add(
        label,
        degenerate,
        detail="Some solids are empty, slivered, or non-manifold — the geometry is "
        "invalid.",
        status="error",
      )
      return False
    self.add(label, len(solids), detail="All solids are well-formed.", status="ok")
    return True

  def check_bed_contact(
    self,
    area: float,
    *,
    label: str = "Bed contact",
    bands: dict[str, tuple[float, Status]] = DEFAULT_CONTACT_BANDS,
    suggestion: str | None = None,
  ) -> Status:
    """Classify a bed-contact footprint ``area`` (mm²) into a difficulty band and
    add the finding. ``suggestion`` (if given) is appended to the detail on
    marginal/failed bands. Returns the band's status so the caller can branch."""
    band, status = _classify_contact(area, bands)
    detail = f"{band.capitalize()} — {_CONTACT_DETAIL[band]}"
    if suggestion and band in _MARGINAL_BANDS:
      detail += f" {suggestion}"
    self.add(label, round(area, 1), unit="mm²", detail=detail, status=status)
    return status

  def check_overlap(
    self,
    a: Compound,
    b: Compound,
    *,
    overlap_label: str = "Parts overlap",
    clearance_label: str = "Clearance",
  ) -> None:
    """Measure the gap between two parts that are meant to stay separate (the
    print-in-place question): an overlap finding (value = interpenetration
    volume) when they touch/interpenetrate, else a clearance finding (value =
    gap). Encapsulates the fuse/gap tolerances."""
    dist, _p1, _p2 = dist_shapes(a.wrapped, b.wrapped)
    if dist <= _OVERLAP_TOL:
      overlap_vol = (a & b).volume
      if overlap_vol >= _OVERLAP_ERROR_MM3:
        detail = "Parts fuse into one solid. Thin them, add sides, or enlarge them."
        status: Status = "error"
      else:
        detail = "Parts touch with no clearance — may fuse. Thin them slightly."
        status = "warning"
      self.add(
        overlap_label, round(overlap_vol, 2), unit="mm³", detail=detail, status=status
      )
    else:
      status = "warning" if dist < _CLEARANCE_WARNING else "ok"
      detail = (
        "Tight gap — parts may fuse."
        if status == "warning"
        else "Healthy gap; parts stay separate."
      )
      self.add(clearance_label, round(dist, 3), unit="mm", detail=detail, status=status)

  def check_floating(
    self,
    shape: Compound,
    *,
    label: str = "Floating sections",
    tol: float = _FLOATING_TOL,
  ) -> None:
    """Flag solids whose lowest point sits above the design's global bed plane
    (nothing anchoring them to the print bed). Value = count of floating solids;
    ``ok`` when none."""
    solids = shape.solids()
    if not solids:
      return
    # Exact boxes: the coarse one (the fast patch's default) can be off by more
    # than `tol`, which would read as a floating section.
    bottoms = [s.bounding_box(optimal=True).min.Z for s in solids]
    z0 = min(bottoms)
    floating = sum(1 for z in bottoms if z > z0 + tol)
    detail = (
      "All sections reach the bed."
      if floating == 0
      else "Sections float above the bed — they need support or a connection."
    )
    self.add(label, floating, detail=detail, status="ok" if floating == 0 else "error")

  # --- Roll-up ------------------------------------------------------------- #

  @property
  def overall_status(self) -> Status:
    if self._status is not None:
      return cast(Status, self._status)
    worst: Status = "ok"
    for item in self._items:
      s = item.get("status")
      if s and _STATUS_RANK[s] > _STATUS_RANK[worst]:
        worst = s
    return worst

  @property
  def summary(self) -> str:
    return (
      self._summary
      if self._summary is not None
      else _DEFAULT_SUMMARY[self.overall_status]
    )

  def to_dict(self) -> dict:
    return {
      "overall_status": self.overall_status,
      "summary": self.summary,
      "items": list(self._items),
    }

  @classmethod
  def error(cls, msg: str) -> "Report":
    """A report that reports only an error (e.g. an analysis that itself failed)."""
    return cls(summary=msg, status="error")


# --------------------------------------------------------------------------- #
# Design — the design seam.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ExportObject:
  """One object in an exported 3MF, for a design whose pieces are meant to stay
  separate (an assembly, not a print-in-place model).

  The default export puts the whole design in a *single* object, which is right
  when everything prints as one piece — its parts can be recoloured but not
  pulled apart. A design that returns these from ``export_objects`` gets one
  slicer object per entry instead: independently selectable, arrangeable and
  deletable, so a user can print the halves in different filaments or drop a
  group entirely.

  ``shape`` sits at its **design** coordinates, which OrcaSlicer shows in the
  Assembly View. ``print_location`` is applied on top of that for the print bed,
  so one file can hold both the assembled arrangement and a flat, printable one
  (``None`` prints where it is designed). ``extruder`` is a 1-based filament
  slot.

  ``parts`` are further volumes of the *same* object — sub-parts in the slicer,
  moving with it, selectable and deletable on their own (a design's break-away
  supports, a section to print in another colour). Each sits at the object's
  design coordinates, like ``shape``, and shares its ``print_location``."""

  name: str
  shape: Shape
  print_location: Location | None = None
  extruder: int | None = None
  parts: Sequence[ExportPart] = ()


@dataclass(frozen=True)
class ExportPart:
  """One sub-part of an ``ExportObject``: a volume the slicer lists under the
  object, at the object's design coordinates."""

  name: str
  shape: Shape
  extruder: int | None = None


@dataclass(frozen=True)
class Preset(Generic[P]):
  """A curated starting point: a name, a one-line description, and a real
  ``Parameters`` instance. Because ``params`` is validated at import time, a
  preset can't drift out of the model's rules, and every value it doesn't care
  about simply keeps the field's default."""

  name: str
  description: str
  params: P


class Design(Compound, Generic[P]):
  """Base for a parametric design. A subclass binds its pydantic params type via
  ``Design[MyParams]``; instantiating it with a validated params instance
  *builds* the geometry into ``self`` (a ``Compound``). Override ``analyze`` for a
  report on the generated shape; set ``PRESETS`` for curated starting points.

  The subclass ``__init__(self, params)`` should store ``self.params = params``,
  build, then call ``super().__init__(...)`` with the resulting children — see
  the ``chain`` design."""

  # The pydantic params class, bound automatically from ``Design[MyParams]`` so
  # the form's JSON schema is available before any geometry is built.
  Parameters: ClassVar[type[BaseModel]]

  # Curated starting points, applied by *replacement*: the form rebuilds from the
  # schema defaults, lays the preset's values over them, then restores the fields
  # marked ``preset_stable=True`` to whatever the user had. So a preset describes
  # one coherent thing (the chain's link shape) while the parameters that are the
  # user's own (how many links, which layout) survive every switch — a preset's
  # ``params`` therefore never has to name a stable field (``presets_json``
  # drops them). ``PRESET_LABEL`` names what is being varied.
  PRESETS: ClassVar[list[Preset[Any]]] = []
  PRESET_LABEL: ClassVar[str] = "Preset"

  # Build and analyze under build123d-utils-jas's fast ``Shape`` patch (see the
  # module docstring and ``fast_patch``). Off by default: it changes
  # ``bounding_box()``'s default precision, which a design has to audit for.
  FAST_PATCH: ClassVar[bool] = False

  def __init_subclass__(cls, **kwargs: Any) -> None:
    super().__init_subclass__(**kwargs)
    for base in getattr(cls, "__orig_bases__", ()):
      origin = get_origin(base)
      if isinstance(origin, type) and issubclass(origin, Design):
        args = get_args(base)
        if args and isinstance(args[0], type):
          cls.Parameters = args[0]

  def analyze(self) -> Report:
    """Findings about the built shape. Default: an empty report."""
    return Report()

  def export_objects(self) -> list[ExportObject] | None:
    """How this design splits into slicer objects on 3MF export.

    Default ``None`` means "one object, one piece" — the whole design exports as
    a single object (its children become that object's parts), which is what a
    print-in-place model wants. A design whose pieces are separate parts to be
    assembled overrides this to return one ``ExportObject`` per piece, so they
    can be arranged, recoloured or deleted individually. See ``ExportObject``."""
    return None

  @classmethod
  def presets_json(cls) -> list[dict]:
    """``PRESETS`` in the form the frontend applies: ``{name, description,
    params}`` with the ``preset_stable`` fields dropped, since those stay the
    user's."""
    stable = {
      name
      for name, f in cls.Parameters.model_fields.items()
      if isinstance(f.json_schema_extra, dict)
      and f.json_schema_extra.get("preset_stable")
    }
    return [
      {
        "name": p.name,
        "description": _unwrap(p.description),
        "params": {
          k: v for k, v in p.params.model_dump(mode="json").items() if k not in stable
        },
      }
      for p in cls.PRESETS
    ]


def load_design(namespace: dict) -> type[Design]:
  """Find the single ``Design`` subclass in a design module's namespace.

  This is the runtime's binding point: a design module is imported/exec'd, then
  its one ``Design`` subclass is located here. Raises ``ValueError`` with a clear
  message if none — or more than one — is present."""
  found: list[type[Design]] = []
  for value in namespace.values():
    if (
      isinstance(value, type)
      and value is not Design
      and issubclass(value, Design)
      and getattr(value, "Parameters", None) is not None
      and value not in found
    ):
      found.append(value)
  if not found:
    raise ValueError(
      "No Design subclass found — define `class MyDesign(Design[MyParams])`."
    )
  if len(found) > 1:
    names = ", ".join(c.__name__ for c in found)
    raise ValueError(f"Expected exactly one Design subclass, found: {names}.")
  return found[0]


# --------------------------------------------------------------------------- #
# Speed — the fast Shape patch, and a cross-build cache for sub-parts.
# --------------------------------------------------------------------------- #

_fast_patch_depth = 0


@contextlib.contextmanager
def fast_patch(enabled: bool = True) -> Iterator[None]:
  """Run the enclosed block under ``build123d_utils_jas.apply_fast_patch()`` when
  ``enabled`` (a design's ``FAST_PATCH``), restoring stock build123d afterwards.
  The patch is a global monkeypatch, so scoping it per call is what keeps a design
  that didn't opt in on stock behaviour. Re-entrant; imported lazily, so nothing
  needs the package unless a design asks for it."""
  global _fast_patch_depth
  if not enabled:
    yield
    return
  from build123d_utils_jas import apply_fast_patch, remove_fast_patch

  if _fast_patch_depth == 0:
    apply_fast_patch()
  _fast_patch_depth += 1
  try:
    yield
  finally:
    _fast_patch_depth -= 1
    if _fast_patch_depth == 0:
      remove_fast_patch()


# Entries kept per memoized function (least recently used dropped first), and
# across all of them — each pins OCC geometry in the WASM heap.
MEMO_ENTRIES_PER_FUNCTION = 8
MEMO_ENTRIES_TOTAL = 48

# One cache per memoized builder, keyed by where it is defined — so reloading a
# design module replaces its builders' caches instead of accumulating dead ones.
_memo_caches: dict[str, OrderedDict] = {}
_memo_hits = 0
_memo_misses = 0


class _Recorder:
  """A stand-in for a ``Parameters`` instance that logs which fields are read.

  A field read is recorded and forwarded. Properties and methods are run with the
  recorder itself as ``self``, so a derived value (``p.hub_r``, ``p.beta(r)``)
  records the fields *it* reads rather than hiding them. Anything else is looked
  up on the real instance, untracked — class-level constants, by the model's
  nature."""

  __slots__ = ("_target", "_cls", "_deps")

  def __init__(self, target: Any) -> None:
    object.__setattr__(self, "_target", target)
    object.__setattr__(
      self, "_cls", target._cls if isinstance(target, _Recorder) else type(target)
    )
    object.__setattr__(self, "_deps", {})

  def __getattr__(self, name: str) -> Any:
    cls = self._cls
    if name in cls.model_fields:
      value = getattr(self._target, name)
      self._deps.setdefault(name, value)
      return value
    attr = inspect.getattr_static(cls, name, None)
    if isinstance(attr, property) and attr.fget is not None:
      return attr.fget(self)
    if isinstance(attr, functools.cached_property):
      return attr.func(self)
    if isinstance(attr, staticmethod):
      return attr.__func__
    if isinstance(attr, classmethod):
      return types.MethodType(attr.__func__, cls)
    if inspect.isfunction(attr):
      return types.MethodType(attr, self)
    return getattr(self._target, name)

  def __setattr__(self, name: str, value: Any) -> None:
    raise TypeError(f"memoized builders must not modify their parameters ({name})")


def _same(a: Any, b: Any) -> bool:
  return type(a) is type(b) and a == b


def _handout(result: Any) -> Any:
  """A caller's own copy of a cached result: shapes are copied (the patched copy
  shares the TopoDS, the stock one duplicates it — cheap either way next to a
  rebuild), so a caller that relabels, recolours or fuses one can't reach back
  into the cache. Tuples/lists are copied element-wise; anything else as is."""
  if isinstance(result, Shape):
    return copy.copy(result)
  if isinstance(result, (tuple, list)):
    return type(result)(_handout(r) for r in result)
  return result


def _trim_memo() -> None:
  total = sum(len(c) for c in _memo_caches.values())
  while total > MEMO_ENTRIES_TOTAL:
    # Drop the oldest entry of the largest cache.
    largest = max(_memo_caches.values(), key=len)
    largest.popitem(last=False)
    total -= 1


def memo(fn: Callable[PS, T]) -> Callable[PS, T]:
  """Cache a sub-part builder ``fn(params, *args)`` across builds.

  A builder takes the whole ``Parameters`` but usually reads a handful of fields
  (a fan blade doesn't care about the shroud), so a key on *all* parameters would
  never hit. Instead the call runs against a recording proxy of ``params`` and
  the entry remembers exactly the fields it read, with their values. A later call
  reuses an entry when every one of those fields still has that value and the
  extra ``args`` are equal — correct under conditional reads, since each entry
  records what *its* run depended on. Entries are LRU-bounded
  (``MEMO_ENTRIES_PER_FUNCTION``, ``MEMO_ENTRIES_TOTAL``) and cleared when a design
  is (re)loaded (``clear_memo``).

  The contract, for the builder:

  * It must be pure in ``params`` and ``args``: same inputs, same geometry.
  * Only attribute reads on ``params`` are tracked (properties and methods of the
    model are followed through). Reading it any other way — ``model_dump()``,
    ``vars(params)``, handing it to code that inspects it wholesale — defeats the
    tracking; ``isinstance(params, Parameters)`` is false on the proxy.
  * Extra ``args`` are compared with ``==``, so they should be plain values
    (strings, numbers, ``Choice`` members). Don't memoize a builder that takes a
    shape.

  Each call hands out a copy of the cached shape(s) (see ``_handout``), so the
  result is the caller's to relabel or modify."""
  cache: OrderedDict[int, tuple[dict[str, Any], tuple, dict[str, Any], Any]] = OrderedDict()
  _memo_caches[f"{fn.__module__}.{fn.__qualname__}"] = cache
  counter = iter(range(1 << 62))

  @functools.wraps(fn)
  def wrapper(params: Any, *args: Any, **kwargs: Any) -> T:  # type: ignore[misc]
    global _memo_hits, _memo_misses
    for key, (deps, e_args, e_kwargs, result) in reversed(cache.items()):
      if (
        len(e_args) == len(args)
        and all(_same(a, b) for a, b in zip(e_args, args))
        and e_kwargs.keys() == kwargs.keys()
        and all(_same(e_kwargs[k], kwargs[k]) for k in kwargs)
        # Read through `params` (not its target), so an enclosing memoized
        # builder records these fields as its own dependencies too.
        and all(_same(getattr(params, name), value) for name, value in deps.items())
      ):
        cache.move_to_end(key)
        _memo_hits += 1
        return _handout(result)
    recorder = _Recorder(params)
    result = fn(recorder, *args, **kwargs)  # type: ignore[arg-type]
    _memo_misses += 1
    cache[next(counter)] = (dict(recorder._deps), args, kwargs, result)
    while len(cache) > MEMO_ENTRIES_PER_FUNCTION:
      cache.popitem(last=False)
    _trim_memo()
    return _handout(result)

  return cast(Callable[PS, T], wrapper)


def clear_memo() -> None:
  """Drop every ``@memo`` entry, releasing the geometry they pin. Called when a
  design is (re)loaded: the old module's builders and ``Parameters`` are gone."""
  for cache in _memo_caches.values():
    cache.clear()


def memo_stats(reset: bool = True) -> dict[str, int]:
  """``{"hits", "misses"}`` of ``@memo`` lookups since the last reset — the
  worker logs them per build."""
  global _memo_hits, _memo_misses
  stats = {"hits": _memo_hits, "misses": _memo_misses}
  if reset:
    _memo_hits = _memo_misses = 0
  return stats
