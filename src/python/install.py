"""Pyodide-only: install the CAD stack via micropip.

Runs once at worker boot, after ``micropip`` and the built-in deps are loaded (see
pyodide.worker.ts). Mocks out OS-level and scientific packages the CAD stack declares
but that are unusable (or unnecessary) in the browser, then installs the WebAssembly
OCP wheels plus build123d and ocp-tessellate — by **direct wheel URL with
``deps=False``**, so no PyPI metadata is fetched and no dependency resolution runs at
boot (every transitive dep is either mocked below, a Pyodide built-in loaded via
``loadPackage`` in the worker, or another URL in ``_WHEEL_URLS``).

``REQUIREMENTS`` is the human-editable source of truth for versions; ``_WHEEL_URLS`` is
generated from it. The pinned ``*-OCP.wasm`` wheels are custom OpenCascade builds
published to PyPI (see github.com/Yeicor/OCP.wasm); their exact post-release stamps are
matched to build123d 0.13.0 (OCP 8) / ocp-tessellate 3.4.2. The JS-side viewer,
three-cad-viewer 5 (an npm dependency), consumes ocp-tessellate's nested-array shapes
format unchanged. Bump the Python set together, then regenerate ``_WHEEL_URLS``:

    node scripts/gen-pyodide-wheels.mjs
"""

import micropip # type: ignore[reportMissingImports]

# Source-of-truth pins. After editing, run: node scripts/gen-pyodide-wheels.mjs
REQUIREMENTS = [
  "cadquery-ocp-novtk-OCP.wasm==8.0.1.1.post202609241841",
  "pydantic==2.12.5",
  "build123d==0.13.0",
  "ocp-tessellate==3.4.2",
  "orca123d==0.1.2",
  "fonttools==4.63.0",
  "build123d-utils-jas==0.2.0",
]

# === MOCKS START (gen-pyodide-wheels.mjs execs this block to resolve REQUIREMENTS) ===
# --- Mock browser-unusable build123d deps our pipeline never exercises ---
# build123d imports these at package load but only touches them on code paths the
# chain generator mostly never hits: IPython for Jupyter-repr helpers, ezdxf for
# DXF import/export (we only build, tessellate, and export STEP/STL/SVG). Each
# stubbed module returns a MagicMock for any attribute (PEP 562 module
# __getattr__), so the load-time imports — and even class-body accesses like
# ``ezdxf.units.MM`` or the ``version=ezdxf.DXF2013`` default — resolve without
# pulling the real wheels and their heavy dependency trees (pygments,
# prompt_toolkit, etc.).
_MAGIC = "from unittest.mock import MagicMock\ndef __getattr__(name):\n    return MagicMock()\n"
micropip.add_mock_package(
  "ipython",
  "8.37.0",
  modules=dict.fromkeys(
    ["IPython", "IPython.lib", "IPython.lib.pretty", "IPython.display"], _MAGIC
  ),
)
# ``ezdxf.colors`` is the one submodule we do exercise: build123d's ExportSVG
# (used for the shape-preview SVGs, see design.py) colour path does
# ``match color: ... case RGB() as rgb:``, and a class pattern requires ``RGB`` to
# be a real class — a MagicMock raises "called match pattern must be a class". So
# give this submodule the real ``RGB``/``aci2rgb`` (a tiny, dependency-free part of
# ezdxf) while everything else stays a MagicMock. ``aci2rgb`` is only reached for
# non-default colours, which this app never uses; the black default hits the
# ColorIndex branch instead.
_EZDXF_COLORS = (
  "from unittest.mock import MagicMock\n"
  "from typing import NamedTuple\n"
  "class RGB(NamedTuple):\n"
  "    r: int = 0\n"
  "    g: int = 0\n"
  "    b: int = 0\n"
  "    def to_floats(self):\n"
  "        return (self.r / 255, self.g / 255, self.b / 255)\n"
  "def aci2rgb(index):\n"
  "    return RGB(0, 0, 0)\n"
  "def __getattr__(name):\n"
  "    return MagicMock()\n"
)
micropip.add_mock_package(
  "ezdxf",
  "1.4.2",
  modules={
    "ezdxf": _MAGIC,
    "ezdxf.colors": _EZDXF_COLORS,
    "ezdxf.math": _MAGIC,
    "ezdxf.units": _MAGIC,
    "ezdxf.entities": _MAGIC,
    "ezdxf.entities.boundary_paths": _MAGIC,
  },
)

# --- Mock the heavy scientific stack (scipy / scikit-learn / sympy) ---
# build123d and its ocp_gordon dependency import these at module load, but only
# ever call into them on code paths the chain generator never touches (convex
# hulls, Voronoi rounds, Bezier fitting, STL primitive detection). They are among
# the largest wheels Pyodide would fetch, so skipping them is the single biggest
# win for worker boot time. Empty mocks are not enough: the load-time
# ``from scipy... import ...`` lines must resolve, so each stubbed module exposes
# exactly the symbols imported at boot. Every stub raises if actually called, so
# an unexpected code path fails loudly instead of returning wrong geometry.
_STUB = "def _stub(*args, **kwargs):\n    raise NotImplementedError('mocked out for WASM boot')\n"
micropip.add_mock_package(
  "scipy",
  "1.18.0",
  modules={
    "scipy": "",
    "scipy.integrate": _STUB + "quad = _stub\n",
    "scipy.optimize": _STUB
    + "minimize = minimize_scalar = _stub\nclass OptimizeResult(dict): pass\n",
    "scipy.spatial": _STUB + "ConvexHull = Voronoi = _stub\n",
  },
)
micropip.add_mock_package(
  "scikit-learn",
  "1.9.0",
  modules={"sklearn": "", "sklearn.cluster": _STUB + "DBSCAN = _stub\n"},
)
micropip.add_mock_package("sympy", "1.14.0", modules={"sympy": ""})

# --- Satisfy build123d's dependency resolution without letting micropip create dummy
# modules that would shadow the real ones the -OCP.wasm wheels provide.
micropip.add_mock_package("cadquery-ocp-novtk", "8.0.1.1.0", modules={})

# --- Mock lib3mf: build123d only uses it for 3MF/STL meshing (b3d.Mesher), a code
# path this app never exercises — STEP export is build123d's own, and 3MF export
# goes through orca123d, which tessellates via OCP's BRep_Tool and writes the zip
# itself (no lib3mf). But build123d/mesher.py does ``from lib3mf import Lib3MF`` at
# package load, so the module must exist for ``import build123d`` to succeed. A
# MagicMock stub keeps that import working while dropping the heavy real
# ``lib3mf-OCP.wasm`` wheel; any accidental use of Mesher would fail loudly
# instead of returning geometry.
micropip.add_mock_package("lib3mf", "2.4.1", modules={"lib3mf": _MAGIC})

# --- Mock threejs-materials: build123d 0.13 imports it at package load
# (exporters3d.py, for PBR materials in glTF export) and bd_materials (a real,
# pure-Python dependency) reaches it lazily when a shape's ``material.pbr`` look is
# resolved. This app assigns no materials and never exports glTF, so neither path
# runs — but the real wheel would pull in Pillow, pygltflib (and its
# dataclasses-json/marshmallow tree) and platformdirs at boot. A MagicMock module
# keeps the load-time ``from threejs_materials import ...`` working.
micropip.add_mock_package(
  "threejs-materials", "1.2.5", modules={"threejs_materials": _MAGIC}
)

# --- Replace Pyodide's built-in typing-extensions (4.15.0, loaded as a pydantic dep
# by the worker's loadPackage) with the PyPI wheel in _WHEEL_URLS: build123d 0.13
# requires >=4.16 and imports ``typing_extensions.sentinel``, which 4.15 lacks. No
# Pyodide 314.x ships a newer one. Uninstalling first lets the resolver in
# gen-pyodide-wheels.mjs pick 4.16 from PyPI, and gives the worker's deps=False
# install a clean slot instead of 4.16 files under a stale 4.15 dist-info. Nothing
# has imported typing_extensions yet at this point. The filter drops micropip's
# harmless "file listed in metadata does not exist" warnings for the built-in.
import logging


def _quiet(record):
  return False


logging.getLogger("micropip").addFilter(_quiet)
micropip.uninstall("typing-extensions")
logging.getLogger("micropip").removeFilter(_quiet)
# === MOCKS END ===

# --- Install the WebAssembly OCP wheels plus build123d and ocp-tessellate ---
# ocp-tessellate does the actual meshing (see runtime.py's show()); its only
# non-mocked deps are pure-python (webcolors / cachetools / imagesize) and numpy,
# which the worker loads via loadPackage before this runs.
#
# GENERATED from REQUIREMENTS by scripts/gen-pyodide-wheels.mjs — do not edit by hand.
# The full closure of the pinned wheels (the five above plus their non-mocked,
# non-builtin transitive deps), each resolved to an exact PyPI URL so install runs with
# deps=False and never hits the PyPI metadata API at boot.
_WHEEL_URLS = [
  # === BEGIN GENERATED WHEEL URLS ===
  "https://files.pythonhosted.org/packages/01/f9/575c8d760eae1fc99651b7cc5efd96ad5379ca4d6b53750b0fb4fe983f34/imagesize-2.0.1-py3-none-any.whl",
  "https://files.pythonhosted.org/packages/0b/84/74f748a3232155a19f09c74cdf7f19cb07623542c48c01a8d06b4f98c836/cadquery_ocp_novtk_ocp_wasm-8.0.1.1.post202609241841-cp314-cp314-pyemscripten_2026_0_wasm32.whl",
  "https://files.pythonhosted.org/packages/13/5e/29c3d8c4b290dee82387468a33ac06c663518a167963cdb9ff48e6bbd8f9/cadquery_ocp_proxy-8.0.1.1.0-py3-none-any.whl",
  "https://files.pythonhosted.org/packages/16/a2/da58cdab8a6978376d66c909b06f4a1c9aafbd23163540741c75a51b675e/build123d-0.13.0-py3-none-any.whl",
  "https://files.pythonhosted.org/packages/1e/a3/bc588e2dd19274f715e95a89f5998098d8fb9301349e2545a84b1042df5e/svgpathtools-1.8.0-py2.py3-none-any.whl",
  "https://files.pythonhosted.org/packages/2a/61/4f22974be46a3d16b11422db01422eddd020e52ee7ab42902add3f8c9c0a/bd_materials-0.2.5-py3-none-any.whl",
  "https://files.pythonhosted.org/packages/2c/47/c99d5268f354002ce80f8d029cd9d7d872969da1de8b93d32de4dc56d6f4/fonttools-4.63.0-py3-none-any.whl",
  "https://files.pythonhosted.org/packages/3e/dc/ffc8307cd048f4f1e28d95cff6fe694af74f2bae771af6da272701d9646a/ocp_gordon-0.3.1-py3-none-any.whl",
  "https://files.pythonhosted.org/packages/48/2c/6c9bb53db56c8a12a736d2158a8b842a5993b96daabc29d90a098e840280/svgelements-1.9.6-py2.py3-none-any.whl",
  "https://files.pythonhosted.org/packages/49/d3/b8441a820a491ddfc024b0b0cf0393375b75ea13866d9c66727e54c2fc80/typing_extensions-4.16.0-py3-none-any.whl",
  "https://files.pythonhosted.org/packages/6c/f5/d07cc8aec1ce477930fda90c981c356c9215f23b65a2108af2d8a8eb101d/ocp_tessellate-3.4.2-py3-none-any.whl",
  "https://files.pythonhosted.org/packages/72/76/20fa66124dbe6be5cafeb312ece67de6b61dd91a0247d1ea13db4ebb33c2/cachetools-5.5.2-py3-none-any.whl",
  "https://files.pythonhosted.org/packages/7b/98/f6aa7fe0783e42be3093d8ef1b0ecdc22c34c0d69640dfb37f56925cb141/anytree-2.13.0-py3-none-any.whl",
  "https://files.pythonhosted.org/packages/d1/8a/6bc5abdae29b244ef55d7629c61105a4d30cc180216f1a00b08692c48796/orca123d-0.1.2-py3-none-any.whl",
  "https://files.pythonhosted.org/packages/d1/f4/c6a34e9d6c329d47e4812c26952c1d6a1cb54ba0594ff0a7ffaa25ec38e8/build123d_utils_jas-0.2.0-py3-none-any.whl",
  "https://files.pythonhosted.org/packages/f0/33/12020ba99beaff91682b28dc0bbf0345bbc3244a4afbae7644e4fa348f23/webcolors-24.8.0-py3-none-any.whl",
  "https://files.pythonhosted.org/packages/f1/a5/4143c35ee979c5decc985ba4621f50fae1ec6977adf8ab0429c2a74be668/ocpsvg-0.7.0-py3-none-any.whl",
  "https://files.pythonhosted.org/packages/ff/8e/43d45cf3e18e3f455e4b5ab333a7c27b8e38c4e535f7346b7148ce08eb65/trianglesolver-1.2-py3-none-any.whl",
  # === END GENERATED WHEEL URLS ===
]

await micropip.install(_WHEEL_URLS, deps=False, keep_going=True)  # type: ignore[no-untyped-call]
