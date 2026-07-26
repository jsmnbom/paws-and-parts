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
* ``MyDesign.analyze(self) -> Report`` — optional; the printability report.
  ``Report``'s ``check_*`` methods carry the generic FDM knowledge.
* ``MyDesign.PRESETS: list[Preset[Parameters]]`` — optional; curated starting
  points, each holding a real (validated) ``Parameters``. ``PRESET_LABEL`` names
  what they vary, in the user's words ("Link shape").

Field helpers (``SliderField`` / ``ChoiceField`` / ``SelectField`` /
``CardsField``) wrap pydantic's ``Field``: they take the validation bounds *and*
the widget hints, and stamp a ``widget`` discriminator (``"slider"`` /
``"shape"`` / ``"select"`` / ``"cards"``) into ``json_schema_extra`` that the
frontend form switches on (see ``src/components/ParamForm.vue``). Which helper
you call is what picks the widget. Add a new parameter type by adding a helper
here and a matching branch there.
"""

from __future__ import annotations

import inspect
import io
import re
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
  TypeVar,
  cast,
  get_args,
  get_origin,
)

import pydantic
from build123d import Compound, ExportSVG, Wire
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
  "FIRST_LAYER_HEIGHT",
  "MAX_OVERHANG_DEG",
  "Status",
  "Report",
  "Preset",
  "Design",
  "load_design",
  "Field",
  "ValidationError",
  "model_validator",
]

T = TypeVar("T")
C = TypeVar("C", bound="Choice[Any]")
P = TypeVar("P", bound="BaseModel")


class BaseModel(pydantic.BaseModel):
  """Base for a design's ``Parameters``. Identical to pydantic's, except that a
  field's **attribute docstring** becomes its schema description — so the help
  text sits under the field it describes instead of inside its constructor."""

  model_config = ConfigDict(use_attribute_docstrings=True)


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
      object.__setattr__(m, "description", rest.strip() or None)
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


def outline_svg(wire: Wire) -> dict:
  """Export a 2D outline wire to a minimal SVG, captured as ``{viewBox, paths}``
  (CAD coords; the frontend flips Y to match SVG's orientation). The frontend
  renders these directly, so previews come straight from the real geometry and
  can never drift from what a build produces.

  ExportSVG pulls in ezdxf only for colour handling; the Pyodide worker mocks
  ezdxf but gives ``ezdxf.colors`` a real ``RGB``/``aci2rgb`` so this path works
  there too (see ``install.py``)."""
  exporter = ExportSVG()
  exporter.add_shape(wire)
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
# other-field -> required value(s)), and ``preset_stable`` (this field belongs to
# the user, not to a preset — see ``Design.PRESETS``).


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
  present (the dropdown small, the cards large) and both work without it."""

  value: str | int
  label: str
  description: str | None = None
  wire: Wire | None = None

  def to_dict(self) -> dict:
    out: dict[str, Any] = {"value": self.value, "label": self.label}
    if self.description:
      out["description"] = self.description
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
    ),
  )


def _as_wire(shape: Any) -> Wire:
  """A preview's outline: a ``Wire`` as-is, else whatever ``.wire()`` gives."""
  return shape if isinstance(shape, Wire) else shape.wire()


# --------------------------------------------------------------------------- #
# Report — a flat list of printability findings.
# --------------------------------------------------------------------------- #
#
# Canonical definition of the report shape; ``protocol.ts``'s
# ``PrintabilityReport`` mirrors ``to_dict()``. Build one imperatively while
# walking the geometry; ``overall_status`` / ``summary`` roll up automatically.
#
# The ``check_*`` methods encapsulate the common FDM printability knowledge
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
_DEFAULT_SUMMARY: dict[str, str] = {
  "ok": "Looks printable.",
  "warning": "Printable, but check the flagged tolerances before printing.",
  "error": "Not printable as-is — resolve the errors below.",
}

# Per-part effective contact footprint (mm²), ordered high -> low.
# band -> (min_area_mm2, item_status); below the smallest min -> _IMPOSSIBLE.
DEFAULT_CONTACT_BANDS: dict[str, tuple[float, Status]] = {
  "easy": (40.0, "ok"),
  "medium": (20.0, "ok"),
  "hard": (10.0, "warning"),
  "expert": (5.0, "warning"),
}
_IMPOSSIBLE: tuple[str, Status] = ("impossible", "error")  # below smallest band

_CONTACT_DETAIL: dict[str, str] = {
  "easy": "Plenty of bed contact.",
  "medium": "Should adhere on most beds.",
  "hard": "Small patch; adhesion may be marginal.",
  "expert": "Very little contact; likely to lift.",
  "impossible": "Too little contact to adhere.",
}
_MARGINAL_BANDS = frozenset({"hard", "expert", "impossible"})

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
  return _IMPOSSIBLE


class Report:
  """A printability report: a flat list of findings plus an auto-derived overall
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
  ) -> bool:
    """Guard against degenerate build output before other checks run: an empty
    build (no solids), zero/near-zero-volume slivers, or an invalid/non-manifold
    BREP. Value = count of degenerate solids (0 -> ok). Returns ``True`` when the
    geometry is sound, so a caller can skip measurements that would be
    meaningless on broken geometry."""
    solids = shape.solids()
    if not solids:
      self.add(
        label,
        0,
        detail="No solid geometry was produced — the build is empty.",
        status="error",
      )
      return False
    degenerate = 0
    for s in solids:
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
        "invalid and won't slice.",
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
    bottoms = [s.bounding_box().min.Z for s in solids]
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
  printability report; set ``PRESETS`` for curated starting points.

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

  def __init_subclass__(cls, **kwargs: Any) -> None:
    super().__init_subclass__(**kwargs)
    for base in getattr(cls, "__orig_bases__", ()):
      origin = get_origin(base)
      if isinstance(origin, type) and issubclass(origin, Design):
        args = get_args(base)
        if args and isinstance(args[0], type):
          cls.Parameters = args[0]

  def analyze(self) -> Report:
    """Printability report for the built model. Default: an empty report."""
    return Report()

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
        "description": p.description,
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
