"""Worker orchestration: tessellate build123d geometry with ocp-tessellate and
post it to the JS viewer, plus schema / build / export helpers for the worker.

Exec'd once at boot, after ``install.py``. It boots *without* a design (the
active design is bound later by ``reload_design``).

``show`` reproduces what ocp-tessellate's ``export_three_cad_viewer_js`` does,
minus the file / ``var name = …`` wrapper: convert to an OcpGroup, tessellate,
inline any shared instance geometry, then post the plain-JSON shapes tree over
the host bridge (``builtins.send_data_to_js``, registered by the worker).
three-cad-viewer's ``render()`` consumes that nested-array form directly, so
there is no decode step on the JS side.
"""

import builtins
import io
import json
import linecache
import sys
import traceback
import types

import build123d as b3d
from ocp_tessellate.convert import tessellate_group, to_ocpgroup
from ocp_tessellate.utils import numpy_to_json
from pydantic import ValidationError

from proto import BaseModel, Design, Report, clear_memo, fast_patch, load_design, memo_stats

# The one Design subclass the active design module defines. All schema / build /
# export / analyze flows go through this handle — the seam, bound in one place
# instead of reaching for loose globals. The engine boots *without* a design
# (``None``); the main thread selects one by slug and calls ``reload_design``,
# which binds it. Worker helpers guard against ``ActiveDesign is None`` until then.
ActiveDesign: type[Design] | None = None

# --------------------------------------------------------------------------- #
# Helpers called by the worker (params cross the boundary as JSON strings)
# --------------------------------------------------------------------------- #


def get_schema_json() -> str:
  """The JSON Schema that drives the form."""
  if ActiveDesign is None:
    return json.dumps({})
  return json.dumps(ActiveDesign.Parameters.model_json_schema())


def get_presets_json() -> str:
  """Curated starting-point presets for the form's preset picker, or ``[]`` when
  the loaded design defines none (see ``Design.PRESETS``)."""
  if ActiveDesign is None:
    return json.dumps([])
  return json.dumps(ActiveDesign.presets_json())


# The module a design's source is loaded as. It is a *real* module (registered in
# ``sys.modules``, with its source in ``linecache``) rather than a bare dict, which
# buys two things: pydantic resolves the design's ``from __future__`` string
# annotations through the module namespace, and it can read each field's attribute
# docstring back out of the source — that is how a field's description reaches the
# form. Natively a design.py is imported as a file and gets both for free.
_DESIGN_MODULE = "design"
_DESIGN_FILE = "<design>"


def reload_design(source: str) -> str:
  """Swap in a new design module without rebooting Pyodide: load ``source`` as the
  ``design`` module, rebind ``ActiveDesign`` to its ``Design`` subclass, and return
  the fresh ``{"ok", "schema", "presets", "preset_label"}`` for the form. Drives
  design selection from the gallery (a bundled design's source) and dev hot-reload
  of a ``design.py``.

  On failure the previous ``ActiveDesign`` (and the previous module) is left
  untouched and ``{"ok": false, "error": <traceback>}`` is returned, so a bad edit
  surfaces its traceback instead of taking the app down."""
  global ActiveDesign, _last_built
  previous = sys.modules.get(_DESIGN_MODULE), linecache.cache.get(_DESIGN_FILE)
  module = types.ModuleType(_DESIGN_MODULE)
  module.__file__ = _DESIGN_FILE
  try:
    # Both must be in place *before* the exec: pydantic resolves annotations and
    # reads attribute docstrings while the class body runs.
    linecache.cache[_DESIGN_FILE] = (
      len(source),
      None,  # no mtime -> checkcache leaves this synthetic entry alone
      source.splitlines(True),
      _DESIGN_FILE,
    )
    sys.modules[_DESIGN_MODULE] = module
    exec(compile(source, _DESIGN_FILE, "exec"), module.__dict__)
    design = load_design(vars(module))
  except Exception:
    prev_module, prev_lines = previous
    sys.modules.pop(_DESIGN_MODULE, None)
    linecache.cache.pop(_DESIGN_FILE, None)
    if prev_module is not None:
      sys.modules[_DESIGN_MODULE] = prev_module
    if prev_lines is not None:
      linecache.cache[_DESIGN_FILE] = prev_lines
    return json.dumps({"ok": False, "error": traceback.format_exc()})
  ActiveDesign = design
  # Nothing built by the previous module is valid for this one: drop the last
  # build export would reuse, and every cached sub-part (they pin OCC geometry).
  _last_built = None
  clear_memo()
  return json.dumps(
    {
      "ok": True,
      "schema": ActiveDesign.Parameters.model_json_schema(),
      "presets": ActiveDesign.presets_json(),
      "preset_label": ActiveDesign.PRESET_LABEL,
    }
  )


def _validation_errors(exc: ValidationError):
  return [
    {"loc": [str(p) for p in e["loc"]], "msg": e["msg"], "type": e["type"]}
    for e in exc.errors()
  ]


def _flatten_edges(shape) -> None:
  """Flatten a shape's ``edges`` to a flat ``[x, y, z, …]`` buffer, in place.

  three-cad-viewer expects ``edges`` as a flat array whenever ``segments_per_edge``
  is present, but ocp-tessellate emits them nested as ``[[x, y, z], …]``. The plain
  render path tolerates either form; the viewer's tools (explode, measurement) read
  the edge buffer directly and require the flat form. Normalizing here keeps both
  paths happy. Idempotent: a flat (1-D) edges array is left untouched."""
  if not isinstance(shape, dict):
    return
  edges = shape.get("edges")
  if shape.get("segments_per_edge") is not None and edges is not None and getattr(edges, "ndim", 1) > 1:
    shape["edges"] = edges.reshape(-1)


def _inline_instances(instances, shapes) -> None:
  """Splice tessellated instance geometry into the shapes tree in place.

  ocp-tessellate dedupes repeated shapes (e.g. every link of a chain shares one
  mesh) behind an instance list, leaving ``{"ref": i}`` placeholders in the
  tree. three-cad-viewer has no ref indirection, so each leaf must carry its own
  geometry. Mirrors ocp-tessellate's own private decode. Each resolved shape is
  also normalized (see ``_flatten_edges``) so the measure / explode tools work."""

  def walk(node):
    if node.get("type") == "shapes":
      shape = node.get("shape")
      if isinstance(shape, dict) and shape.get("ref") is not None:
        shape = instances[shape["ref"]]
        node["shape"] = shape
      _flatten_edges(shape)
    for part in node.get("parts") or []:
      walk(part)

  walk(shapes)


def show(obj, progress=None) -> None:
  """Tessellate ``obj`` and post the shapes tree to the JS viewer."""
  group, instances = to_ocpgroup(obj)
  instances, shapes, *_ = tessellate_group(group, instances, progress=progress)
  _inline_instances(instances, shapes)
  builtins.send_data_to_js(numpy_to_json(shapes), "DATA")  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- #
# The build, in phases
# --------------------------------------------------------------------------- #
#
# A build is three separate calls rather than one — geometry, tessellation,
# analysis — because the worker's event loop is blocked for the whole of each
# call. Splitting them gives the worker a moment *between* phases to notice that
# newer parameters have arrived and abandon the rest of the work (there is no way
# to interrupt a phase from the outside: OCP's C++ calls have no interrupt
# points, and CPython's would need a SharedArrayBuffer we can't have). The phases
# also give the UI something honest to draw a progress bar from.
#
# The worker drives them in order and is responsible for calling ``build_abandon``
# on any path that doesn't reach ``build_analyze``.

# The object built by ``build_geometry``, waiting to be tessellated/analyzed, and
# the validated parameters it was built from. It pins OCC memory between calls, so
# every exit path clears it.
_staged: Design | None = None
_staged_params: BaseModel | None = None

# The last build that ran to completion — what the viewer is showing — as
# ``(design class, parameters, object)``. ``export_bytes`` reuses it when asked for
# exactly those parameters instead of building them again. Only a finished build
# replaces it (an abandoned or failed one leaves it alone); a design reload clears
# it. Pins one design's geometry between builds: the same object that is on screen.
_last_built: tuple[type[Design], BaseModel, Design] | None = None


def build_geometry(params_json: str) -> str:
  """Phase 1 — validate the parameters and build the geometry, staging the result
  for the phases that follow. Returns ``{"ok": true}``, or ``{"ok": false,
  "errors": [...]}`` for inline display on the form."""
  global _staged, _staged_params
  _staged = None
  _staged_params = None

  if ActiveDesign is None:
    return json.dumps({"ok": False, "errors": [{"loc": [], "msg": "No design loaded", "type": "no_design"}]})

  data = json.loads(params_json)
  try:
    params = ActiveDesign.Parameters(**data)
  except ValidationError as exc:
    return json.dumps({"ok": False, "errors": _validation_errors(exc)})

  memo_stats()  # reset, so the counts below are this build's
  try:
    with fast_patch(ActiveDesign.FAST_PATCH):
      _staged = ActiveDesign(params)  # type: ignore[reportGeneralTypeIssues]
  except Exception as exc:  # geometry error -> surface as a form-level message
    return json.dumps(
      {"ok": False, "errors": [{"loc": [], "msg": str(exc), "type": "build_error"}]}
    )
  _staged_params = params
  # `memo`: this build's sub-part cache hits/misses, for the worker's timing log.
  return json.dumps({"ok": True, "memo": memo_stats()})


def build_tessellate() -> str:
  """Phase 2 — tessellate the staged geometry and post it to the viewer (a side
  effect of ``show``, over the host bridge)."""
  if _staged is None:
    return json.dumps({"ok": False, "errors": [{"loc": [], "msg": "Nothing staged", "type": "build_error"}]})
  show(_staged, progress=None)
  return json.dumps({"ok": True})


def build_analyze() -> str:
  """Phase 3 — the analysis report, and the end of the build. Never lets an
  analysis failure break a build whose geometry is already on screen."""
  global _staged, _staged_params, _last_built
  if _staged is None or ActiveDesign is None:
    return json.dumps({"ok": False, "errors": [{"loc": [], "msg": "Nothing staged", "type": "build_error"}]})
  try:
    with fast_patch(ActiveDesign.FAST_PATCH):
      report = _staged.analyze().to_dict()
  except Exception as exc:
    report = Report.error(f"Analysis failed: {exc}").to_dict()
  if _staged_params is not None:
    _last_built = (ActiveDesign, _staged_params, _staged)
  _staged = None
  _staged_params = None
  return json.dumps({"ok": True, "report": report})


def build_abandon() -> str:
  """Drop the staged geometry without finishing the build — the parameters it was
  built from are already out of date."""
  global _staged, _staged_params
  _staged = None
  _staged_params = None
  return json.dumps({"ok": True})


def export_bytes(params_json: str, fmt: str, name: str):
  """Build ``params_json`` and return the exported file as ``bytes``. Exports
  always match the parameters they were asked for, even when the last render was
  for different ones — but when the last *finished* build was for exactly these
  (pydantic equality, so key order and float formatting don't matter), that
  object is exported as is instead of being built again (see ``_last_built``).
  ``name`` (the params-encoding basename the frontend already computed for the
  download filename, see ``exportFilename`` in ``paramsUrl.ts``) is reused as the
  3MF project's object name too, so the two stay in sync without recomputing the
  same "which params differ from default" logic in Python."""
  if ActiveDesign is None:
    raise RuntimeError("No design loaded")

  data = json.loads(params_json)
  params = ActiveDesign.Parameters(**data)
  if _last_built is not None and _last_built[0] is ActiveDesign and _last_built[1] == params:
    shape = _last_built[2]
  else:
    with fast_patch(ActiveDesign.FAST_PATCH):
      shape = ActiveDesign(params)  # type: ignore[reportGeneralTypeIssues]

  fmt = fmt.upper()
  if fmt == "STEP":
    bio = io.BytesIO()
    b3d.export_step(shape, bio)
    return bio.getvalue()
  if fmt == "3MF":
    # OrcaSlicer project via orca123d. save() writes to a path (it tessellates
    # into a zip of model + settings XML), so round-trip through Pyodide's
    # in-memory FS rather than a BytesIO.
    from orca123d import Project

    proj = Project()
    # A design whose pieces are separate parts to be assembled splits itself into
    # one slicer object per piece (see `Design.export_objects`), so they can be
    # arranged, recoloured or deleted individually — and each can sit at a
    # different place for printing than it does in the Assembly View. Everything
    # else is one object: the design as a single printed piece.
    objects = shape.export_objects()
    if objects:
      for obj_spec in objects:
        obj = proj.add_object(
          obj_spec.shape,
          name=obj_spec.name,
          extruder=obj_spec.extruder,
          print_location=obj_spec.print_location,
        )
        for part_spec in obj_spec.parts:
          obj.add_part(part_spec.shape, name=part_spec.name, extruder=part_spec.extruder)
    elif len(shape.children) > 0:
      obj = proj.add_object(None, name=name)
      for child in shape.children:
        obj.add_part(child)
    else:
      proj.add_object(shape, name=name)
    path = "/tmp/export.3mf"
    proj.save(path)
    with open(path, "rb") as f:
      return f.read()
  raise ValueError(f"Unknown export format: {fmt}")
