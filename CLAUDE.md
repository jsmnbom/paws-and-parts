# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**build123d-lab** — a browser playground for parametric 3D-printable **designs**.
Everything runs client-side: Python (build123d) executes in a web worker via
Pyodide + OCP.wasm, geometry is tessellated with ocp-tessellate and rendered with
three-cad-viewer. No server. See `README.md` for the stack table.

**Engine vs. content.** `build123d-lab` is the reusable engine (the Vue app shell,
`ParamForm`/`Viewer`, worker, protocol, `proto.py` SDK, `runtime.py`, and the
build/deploy/wheel tooling). **Paws & Parts** is this repo's content: the
`src/designs/` gallery + `src/branding.ts`. The engine depends only on two seams —
the **design manifest** (`src/designs/manifest.ts`) and the **branding config**
(`src/branding.ts`) — never on a specific design's internals, so it stays cheap to
extract later. A single design ("chain") is just one bundled entry.

## Commands

Toolchain is managed with [mise] (`.mise.toml` pins Node 25, pnpm 10, Python 3.13, uv).

```sh
mise install          # toolchain
pnpm install          # JS/TS deps
uv sync --extra dev   # Python dev env, for authoring/type-checking src/python natively

pnpm dev              # Vite dev server (http://localhost:5173)
pnpm typecheck        # vue-tsc --noEmit
pnpm build            # typecheck + production build to dist/
uv run ruff format src/python   # formatting only (see below)
```

The user runs `ruff format` and tests themselves — don't run them. To verify a
change, instruct the user how to exercise it (dev server, or the native
round-trip below), rather than running a test suite (there is none).

Exercise the Python contract natively — same source the worker runs (`design.py`
is imported by name, with `proto.py` on `sys.path`, so pydantic can resolve the
design's forward refs):

```sh
uv run python -c "import sys; sys.path.insert(0,'src/python'); \
  sys.path.insert(0,'src/designs/chain'); import proto, design; \
  D = proto.load_design(vars(design)); print(D(D.Parameters()))"
```

## The single-source-of-truth contract

To add a design, follow `.claude/skills/add-design/SKILL.md` (the `add-design`
skill): it is one new `src/designs/<slug>/design.py`, nothing else to register.

The seam is defined in `src/python/proto.py` (the design-authoring **SDK**) and
implemented by each `src/designs/<slug>/design.py` (chain is the reference one).
`proto.py` is the one place that states the whole contract — read its module
docstring first.

A design module exposes exactly one **`Design` subclass**:

- `class Chain(Design[Parameters])` — the generic arg binds the pydantic
  `Parameters` type (auto-extracted in `Design.__init_subclass__`), so the form
  schema is available via `Chain.Parameters.model_json_schema()` before any
  geometry exists.
- **Instantiating** `Chain(params)` *builds* the geometry into `self` (a
  build123d `Compound`, like `BasePartObject`) — construction **is** the build.
- `Chain.analyze(self) -> Report` (optional) returns the printability report,
  reading `self` directly. `Chain.PRESETS` (optional) are curated starting points
  — `Preset(name, description, params=Parameters(...))`, so each holds a *real
  validated* `Parameters` and everything it doesn't name keeps the field default —
  titled by `PRESET_LABEL` ("Link shape"). A preset is applied by **replacement**
  — schema defaults, the preset's `params` over them, then every field flagged
  `preset_stable=True` (a `json_schema_extra` hint: the chain's link count,
  layout, center skip) restored to the user's value. `Design.presets_json()` drops
  the stable fields on the way out, so a preset can never name one. Designs declare
  their stable fields first, so the form can rule off where the preset's territory
  starts.
- Gallery metadata is the module **docstring** (first line = name, rest =
  description) plus optional `__author__`, `__urls__` (`{label: url}` → per-design
  footer links, icons auto-derived from the URL host), `__blank__ = True` and
  `__experimental__ = True` — the latter marking a design that isn't finished
  (geometry/params still moving, not print-verified): it gets an "Experimental"
  badge on its gallery card *and* in the generator header, and sorts after the
  finished designs in the gallery.
  `__urls__` is for *outbound* links only: `manifest.ts` appends a "Source" link
  pointing at that design's own `design.py` at the build's commit (`__GIT_HASH__`,
  `HEAD` in a dev build), so no design hardcodes a repo URL.
  `manifest.ts` scrapes them from the source text for the gallery card / generator
  footer (co-located so they can't drift; no Python boot needed to read them).
- The generator header has a **Screenshot** button that saves a clean PNG of the
  current view (via the viewer's `captureScreenshot`, axes/marker stripped). It's a
  general-purpose save, and also the way to make a design's gallery-card
  **thumbnail**: an optional `src/designs/<slug>/thumb.png` (globbed by
  `manifest.ts`; cards fall back to a box icon when absent) — take a screenshot and
  rename it to `thumb.png` in the design's directory.

`runtime.py` binds to this via `proto.load_design(namespace)`, holding the active
subclass as `ActiveDesign` — it never reaches for loose `build`/`analyze` globals.
The engine boots **without** a design; the frontend selects one by slug and
`reload_design(source)` loads it as the `design` module and rebinds `ActiveDesign`.
Keep the contract clean; don't leak chain-specifics into
`proto.py`/the worker/runtime/frontend.

- Cross-field rules (`link_width >= link_thickness`, etc.) live in a pydantic
  `@model_validator(mode="after")`, **not** duplicated in JS. The form learns
  about violations by attempting a build and rendering the returned
  `ValidationError` inline. There is no separate client-side validation.
- A parameter is declared with one of `proto.py`'s field helpers — `SliderField` /
  `ChoiceField` / `SelectField` / `CardsField` / `ToggleField` — which wrap
  pydantic's `Field`: they take the validation bounds *and* the widget hints,
  stamping a `widget` discriminator
  (`"slider"`/`"shape"`/`"select"`/`"cards"`/`"toggle"`) into
  `json_schema_extra` that the form switches on. **The helper you call is what
  picks the widget**; `Option(value, label, description, wire=...)` is the shared
  option type for `SelectField`/`CardsField` (`wire` is an optional preview both
  render). A field's **attribute docstring** is its description — `proto.BaseModel`
  sets `use_attribute_docstrings=True`, which is why `reload_design` must give the
  design a real module identity (below). Add a new parameter type by adding a
  helper there + a branch in `ParamField.vue`. All four also take
  `recenter=True` (the chain's `layout`): the viewer normally holds the user's
  camera across rebuilds, but a flagged field — like applying a preset, which
  always does it — reframes on the new geometry instead (`Viewer.resetView()`);
  and `advanced=True` (the knob's fillets/tolerances), which files the field under
  a closed "Advanced" disclosure at the end of the form instead of the main run of
  controls — a display hint, not a parameter, so nothing extra reaches the params
  object, the URL or the export filename. "How to show the form" stays
  co-located with the design. Schema→field resolution is in
  `src/components/ParamForm.vue` (grouping, `show_if`, dependent bounds) and one
  field is drawn by `ParamField.vue`, over the `Field` union in
  `src/lib/formFields.ts`; `src/lib/resolveSchema.ts` flattens pydantic's
  `$ref`/`$defs` first. Help text is written hard-wrapped and **unwrapped** by
  `proto` on the way out (`_unwrap`: paragraph breaks and list items kept — field
  descriptions via `BaseModel.model_json_schema`, plus `Choice`/`Option`/preset
  descriptions), so the form shows it `whitespace-pre-line` in an info popover
  (hover + click on desktop, tap elsewhere). A changed field shows a revert-to-
  default button (`Field.default`); "Reset all" sits in the preset strip and the
  compact header menu.
- The printability report is a flat list of findings built with `proto.Report`
  (`r.add(label, value, ...)`, auto-rolled `overall_status`/`summary`; `value` is
  numeric). Its `to_dict()` is mirrored by `PrintabilityReport` in `protocol.ts` and
  rendered generically by `PrintabilityPanel.vue`. The generic checks are `Report`
  methods (`check_geometry` / `check_bed_contact` / `check_overlap` /
  `check_floating` — each adds a finding, owning default thresholds/wording any
  design can reuse), alongside the printing constants `FIRST_LAYER_HEIGHT` /
  `MAX_OVERHANG_DEG`; design-specific choices (which face is the footprint, brim
  messaging, the chain's link lean) stay in the design's `analyze`.
- **Speed, opt-in per design.** `Design.FAST_PATCH = True` (chain, infinity-cube)
  runs build + analyze under `build123d_utils_jas.apply_fast_patch()`, scoped per
  call by `proto.fast_patch(enabled)` so other designs keep stock build123d. The
  patch makes `copy`/`moved` share the TopoDS and `bounding_box()` default to the
  **coarse** box — pass `optimal=True` wherever a box feeds a threshold
  (`check_floating` does). Separately, `@proto.memo` caches a pure sub-part builder
  `f(params, *args)` across builds, keyed on the fields it *actually read* (a
  recording proxy of `params`; properties/methods are followed through, but
  `model_dump()`/`vars()` defeat it) and handing out copies. `reload_design` calls
  `clear_memo()`; per-build hits/misses reach the `build-timing` console log.
  `Design.analyze`'s default is an empty report — a design opts into checks by
  calling the methods.
- `proto.py`'s `Choice` is a lightweight enum-like whose members are static
  methods (docstring → label/description) and whose SVG previews are generated
  from the *real* build123d outline, so previews can't drift from the geometry.

## Runtime architecture

Data flows main thread ↔ worker over a typed message protocol in
`src/lib/protocol.ts` (`WorkerRequest`/`WorkerResponse`). Read it first when
touching cross-boundary code.

- **Routing / views.** `src/main.ts` uses `createWebHistory` (real paths, no hash;
  `BASE_URL` carries the Pages subpath). `/` → `src/views/Gallery.vue` (design cards
  from `manifest.ts`); `/m/:slug` → `src/views/Generator.vue` (the form+viewer for
  one design). `App.vue` constructs the **one** worker at the app root and
  `provide()`s it (`designWorkerKey`) so its OCP boot warms while the gallery is
  browsed; both views inject it. `dist/404.html` (a copy of `index.html`, emitted by
  the `spaFallback` Vite plugin) is the GitHub Pages SPA deep-link fallback.
- `src/worker/pyodide.worker.ts` — boots Pyodide once, then serves requests. It
  `exec`s the Python assets in order: `install.py` → writes `proto.py` to the FS
  (it must be *importable*, the rest are exec'd into globals) → `runtime.py`. It
  boots with **no design**; the main thread sends `reload-design` (a bundled
  design's source) to bind one. A `host_bridge` JsModule lets
  Python post the tessellated Shapes tree straight to the main thread. Scheduler
  runs one Python call at a time: design reloads and builds **coalesce to
  latest-wins**, exports are FIFO. A build runs as **three Python calls**
  (`runBuild`: geometry → tessellate → analyze) with a macrotask yield between
  them, so a build the user has already superseded is **abandoned at the next
  phase boundary** (`build_abandon`, a `build-cancelled` reply, no frame). Those
  boundaries are the only place cancellation is possible at all: while Python
  runs, the worker's event loop is blocked, and OCP's C++ calls have no interrupt
  points (CPython's own would need a `SharedArrayBuffer`, i.e. cross-origin
  isolation, which GitHub Pages can't give us).
- `src/python/runtime.py` — holds the active seam as `ActiveDesign` (bound by
  `reload_design`), and exposes worker-facing helpers: `get_schema_json`, the
  build phases `build_geometry` (validate → `ActiveDesign(params)`, staged in
  `_staged`) / `build_tessellate` / `build_analyze` / `build_abandon`,
  `export_bytes` (STEP via build123d; 3MF OrcaSlicer project via
  orca123d; reuses the last *finished* build, `_last_built`, when the requested
  params equal it, else builds), and `reload_design` (gallery select / dev hot-reload; returns
  `{ok, schema?, presets?, error?}`, surfacing a bad edit's traceback instead of
  throwing). It loads the source as a **real `design` module** (registered in
  `sys.modules`, source in `linecache`) rather than exec'ing it into a dict: that
  is what lets pydantic resolve the design's string annotations *and* read each
  field's attribute docstring back out of the source. A failed load restores the
  previous module and leaves `ActiveDesign` bound.
- `src/composables/useDesignWorker.ts` — worker lifecycle + reactive state (schema,
  shapes, `buildStage`/`buildPending`, fieldErrors, `buildError` (the form-level
  message), `shapesStale` (latest build failed, viewer shows an older result),
  report, `reloadError`, `reloading`), debounced `build` and `reloadDesign`, export. `build`'s debounce is
  asymmetric: a change following a lull (> `BUILD_IDLE_GAP_MS`) is a deliberate act
  and dispatches immediately, one on the heels of another is part of a burst and
  only its settling value is built (`BUILD_TRAILING_MS`). `buildStage` is what the
  worker is doing right now; `BuildProgress.vue` draws it as a hairline bar over
  the viewer (hidden for the first 200 ms, so quick rebuilds pass unremarked).
- `src/components/` — `BootProgress`, `BuildProgress` (the per-build bar, distinct
  from the boot bar), `ParamForm` (+ `ParamField`, one control), `Viewer`
  (three-cad-viewer
  wrapper), `PrintabilityPanel`, `ShapeSelect`, `ShapePreview`, `AppContent` (the
  form + viewer body), `CodeEditor` (lazy Monaco; loads only when the source view is
  first opened, so Monaco never touches the main bundle). The source view is
  **read-only** and presented as a layer that slides over the form inside the
  controls pane (a "Code" button in the header opens it; ✕ / Esc closes it) — live
  edits hot-swapping the design on every keystroke outran the error surface, so
  `CodeEditor` doesn't emit and `AppContent` doesn't call `reloadDesign`. With no
  authoring path, `BLANK` designs are filtered out of the gallery
  (`src/views/Gallery.vue`) while still reachable at `/m/<slug>`.
- Failures are loud: a build error is an alert at the *top* of `ParamForm`, a
  toast (`AppContent`, one per distinct message; also for "Analysis failed" and
  export failures) and, while `shapesStale`, a banner over the viewer whose "Show
  error" scrolls the form to it.
- `localStorage` keys: `lab-params:<slug>` (the last non-default params per design,
  in the URL's encoding; seeded as defaults → stored → URL query, cleared by
  "Reset all"), `lab-export-format`, `lab-layout` (pane sizes).
- `vite.config.ts`'s `tcvIcons()` also strips three-cad-viewer's page-level
  `body { overflow: hidden; user-select: none }` rule, which otherwise stays loaded
  after visiting a generator and stops the gallery scrolling.

Full regeneration on every parameter change (no incremental updates) — each
change re-runs `ActiveDesign(params)` and re-tessellates the whole design. First
load compiles OCP.wasm (tens of seconds); the boot progress bar covers it and is a
separate UI state from the sub-second per-build bar (`BuildProgress.vue`).

In dev, editing a `src/designs/<slug>/design.py` triggers a Vite page reload;
because the **SharedWorker survives page reloads**, the page reconnects to the
already-booted backend (no Pyodide reboot) and the Generator re-selects the fresh
source via `reload-design` — effectively a hot-swap with no boot bar. Editing an engine asset (worker/`proto.py`/`runtime.py`/…) still does a full
reload *and* reboots the interpreter (those are in the worker's import graph).

## Version pinning — these move together

The Python packages are installed **in the browser by exact wheel URL with
`deps=False`** (`src/python/install.py`), so no dependency resolution runs at
boot. `REQUIREMENTS` is the human-editable pin list; `_WHEEL_URLS` is generated.
The `-OCP.wasm` wheels are custom OpenCascade builds matched to specific
build123d / ocp-tessellate / three-cad-viewer versions. When bumping any of
these, bump them **as a set**, then regenerate:

```sh
pnpm gen:wheels   # node scripts/gen-pyodide-wheels.mjs — resolves + rewrites _WHEEL_URLS
```

Coupled invariants to keep in sync by hand:
- `PYODIDE_VERSION` in `pyodide.worker.ts` **must equal** the `pyodide`
  devDependency (`gen:wheels` guards this).
- The worker's `loadPackage([...])` list must cover the built-in deps the pinned
  wheels need (they're installed `deps=False`); `gen:wheels` prints the required
  built-in roots.
- `build123d-utils-jas` (the fast patch, `printing` fastener helpers) is pure
  Python; it imports scipy at load, which the mock block already stubs.
- `typing-extensions`: build123d 0.13 needs ≥ 4.16 (`typing_extensions.sentinel`)
  but every Pyodide 314.x ships 4.15 as a built-in (a pydantic dep), so the mock
  block `micropip.uninstall`s the built-in and `_WHEEL_URLS` carries the PyPI
  wheel. Drop that once Pyodide's own is new enough.
- The Pyodide built-ins also bound pure-Python pins: `gen:wheels` fails when a
  wheel requires a newer built-in than this Pyodide has (orca123d 0.1.x needs
  numpy ≥ 2.4.6, which is why Pyodide is on 314.0.7, not 314.0.2).
- `pyproject.toml` deps mirror the browser pins so native authoring/type-checking
  hits the same API surface. `.venv` is not what runs in the browser.
- The `# === MOCKS ===` block in `install.py` stubs out browser-unusable/heavy
  deps (IPython, ezdxf, scipy/sklearn/sympy, lib3mf, threejs-materials) that
  build123d imports at load but this app's code paths don't exercise (keep
  `MOCKS` in `gen-pyodide-wheels.mjs` in step). `gen:wheels` reads this block to
  resolve requirements, so keep the markers intact.

## Linting / type-checking

`ruff` is **formatting only** — `[tool.ruff.lint] select = []` makes `ruff check`
a no-op. Type-checking of `src/python` is Pylance (Pyright) in the editor (see
`.vscode/settings.json`), against `cadquery-ocp-stubs`.

[mise]: https://mise.jdx.dev
