# Paws & Parts

A browser playground for parametric 3D-printable designs, running entirely
client-side. Python ([build123d]) runs in the browser via [Pyodide] + [OCP.wasm];
geometry is tessellated with ocp-tessellate and rendered with [three-cad-viewer].
No server. Paws & Parts is the design gallery; **build123d-lab** is the reusable
engine underneath it.

## Stack

| Concern            | Choice                                            |
| ------------------ | ------------------------------------------------- |
| App framework      | Vue 3 + Vite (plain, client-side only)            |
| UI components      | Nuxt UI (standalone Vue mode) + Tailwind CSS      |
| Python runtime     | Pyodide **v314.0.2**, in a web worker               |
| CAD kernel         | OCP.wasm (OpenCascade compiled to WebAssembly)    |
| CAD library        | build123d **0.11.1**                              |
| Tessellation       | ocp-tessellate (via ocp_vscode **3.1.2**)         |
| 3D viewer          | three-cad-viewer **5.0.0** (bundles three **0.184.0**) |
| Parameter modeling | pydantic                                          |

The pinned versions above move together — see `src/python/install.py`.

## Toolchain

Managed with [mise] (Node, pnpm, Python, uv) — `.mise.toml` pins the versions.

```sh
mise install          # Node 25, pnpm 10, Python 3.13, uv
pnpm install          # JS/TS dependencies
uv sync --extra dev   # Python dev env (build123d + pydantic) for authoring/type-checking src/python
```

## Develop

```sh
pnpm dev              # Vite dev server (http://localhost:5173)
pnpm typecheck        # vue-tsc
pnpm build            # type-check + production build to dist/
```

Each design in `src/designs/<slug>/design.py` (a `Design` subclass; see
`src/python/proto.py`) can be exercised natively — it's the same source the worker
runs:

```sh
uv run python -c "import sys; sys.path.insert(0,'src/python'); \
  sys.path.insert(0,'src/designs/chain'); import proto, design; \
  D = proto.load_design(vars(design)); print(D(D.Parameters()))"
uv run ruff format src/python   # ruff is formatting-only; type-checking is Pylance (see .vscode/settings.json)
```

## Architecture

- **`src/python/proto.py`** — the design-authoring SDK: the `Design` seam, the
  `Report` builder, and the field helpers. States the whole contract.
- **`src/designs/<slug>/design.py`** — one design: a single `Design` subclass that
  is the source of truth for both the form and the geometry (`chain` is the
  reference one). `src/designs/manifest.ts` bundles them for the gallery.
- **`src/python/install.py`** — one-time micropip install of the CAD stack in Pyodide.
- **`src/python/runtime.py`** — binds the seam (`load_design`), routes ocp_vscode
  tessellation through a host bridge (no socket), and exposes schema / build /
  export / hot-reload helpers.
- **`src/worker/pyodide.worker.ts`** — loads Pyodide + the CAD stack once, then
  serves debounced, latest-wins build requests and STEP exports.
- **`src/composables/useDesignWorker.ts`** — worker lifecycle + reactive state.
- **`src/components/`** — `BootProgress`, `ParamForm` (generated from the JSON
  schema), `Viewer` (three-cad-viewer wrapper).

First load downloads and compiles OCP.wasm (tens of seconds); a boot progress bar
covers it. After that, each parameter change re-runs `build()` and re-tessellates
the whole chain.

## AI use disclaimer

In the interest of transparency about its use: I used AI primarily for tedious,
mechanical work — large refactors, renames, boilerplate — where the outcome is
easy to verify. I also leaned on it for implementation *inspiration*, but in
practice much of that was reverted: I'd take the idea, decide I preferred a
different approach, and reimplement it my own way. The design decisions and the
final shape of the code are mine.

[build123d]: https://github.com/gumyr/build123d
[Pyodide]: https://pyodide.org
[OCP.wasm]: https://github.com/Yeicor/OCP.wasm
[three-cad-viewer]: https://github.com/bernhard-42/three-cad-viewer
[mise]: https://mise.jdx.dev
