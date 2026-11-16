---
name: add-design
description: How to add a new parametric design to the build123d-lab gallery (src/designs/<slug>/design.py). Use when asked to scaffold, add, register, or set up a new design/model in this repo.
---

# Adding a design

A design is one directory, `src/designs/<slug>/`, holding `design.py` and an
optional `thumb.png`. **That is the whole registration step.** `manifest.ts`
globs `./*/design.py` and scrapes the metadata from the source text, so no
manifest, router, or icon edit is needed. The design is reachable at
`/m/<slug>` and its gallery card sorts finished → experimental → blank.

The contract is `src/python/proto.py` — read its module docstring before writing
a design. `src/designs/blank/design.py` is the minimal copy-me;
`src/designs/infinity-cube/design.py` is the fleshed-out multi-piece reference,
`src/designs/chain/design.py` the finished single-piece one.

## Scaffolding steps

1. Copy `blank/design.py` to `src/designs/<slug>/design.py`. Drop `__blank__`.
2. Write the module docstring: first line is the gallery name, the rest the
   description. Add `__author__`, `__urls__` (outbound links only; the source
   link is appended automatically) and `__experimental__ = True` until it is
   print-verified.
3. Declare `Parameters(BaseModel)`: one field per control, each followed by an
   attribute docstring (that is the help popover). Put `preset_stable` fields
   first. Cross-field rules go in a `@model_validator(mode="after")` raising
   `ValueError` with actionable text — there is no client-side validation.
4. Define exactly one `Design[Parameters]` subclass. Its `__init__(self,
   params)` stores `self.params`, builds, then calls
   `super().__init__(None, label=..., children=[...])`. Construction *is* the
   build.
5. Optional: `PRESETS` (`Preset(name, description, params=Parameters(...))`)
   with `PRESET_LABEL`; `analyze()` returning a `Report`; `export_objects()` for
   multi-piece 3MF; `FAST_PATCH = True`; `@memo` on pure sub-part builders.
6. Verify (below), then screenshot for the thumbnail.

## Field helpers (`proto.py`)

The helper you call picks the widget. All take `name`, `default`, and the
display flags at the end.

- `SliderField` — numeric. `ge/gt/le/lt` are hard bounds, `slider_min/max`
  narrow the slider, `step`, `unit`. `int` annotation + `step=1` for counts.
- `ChoiceField` — picker over a `Choice` class whose members are static
  methods; previews are generated from real build123d outlines.
- `SelectField` — dropdown over `Option(value, label, description, wire=...)`.
- `CardsField` — same options as big tiles; keep to 2–3.
- `ToggleField` — a `bool`.

Flags on any helper: `preset_stable=True` (survives preset switches; a preset
may never name it), `recenter=True` (reframe the camera on change),
`advanced=True` (files under the closed "Advanced" disclosure), `show_if={...}`
(conditional display), `slider_max_by` (dependent bound), `size`.

## Rules that bite

- A preset that names a `preset_stable` field fails at import.
- Help text is unwrapped on the way out: hard-wrap freely, paragraph breaks and
  list items survive.
- `FAST_PATCH` makes `bounding_box()` coarse — pass `optimal=True` wherever a box
  feeds a threshold.
- `@memo` builders must be pure `f(params, *args)` and read fields as attributes;
  `model_dump()`/`vars()` defeat the tracking.
- Multi-piece designs: give every placed piece a `label` and `color`, and return
  `ExportObject`s with a `print_location` that lands each one flat.
- Report findings are numeric `value`s; use `check_geometry` first and return
  early when it fails.
- For the build123d work itself, use the `geometry-ocp` skill.

## Verify

Native round-trip, the same source the worker runs (swap the slug):

```sh
uv run python -c "import sys; sys.path.insert(0,'src/python'); \
  sys.path.insert(0,'src/designs/<slug>'); import proto, design; \
  D = proto.load_design(vars(design)); print(D(D.Parameters())); \
  print(D.presets_json()); print(D(D.Parameters()).analyze().to_dict())"
```

Then `pnpm dev` and open `/m/<slug>`. Saving `design.py` hot-swaps the design
without an OCP reboot. The user runs `uv run ruff format` themselves.

Thumbnail: the generator header's **Screenshot** button saves a clean PNG; rename
it to `src/designs/<slug>/thumb.png`.
