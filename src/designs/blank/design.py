"""Start from scratch

A minimal Design scaffold to build your own model on.
"""

from build123d import *  # pyright: ignore[reportWildcardImportFromLibrary]
from proto import *  # pyright: ignore[reportWildcardImportFromLibrary]

# The docstring above is this design's gallery copy: first line the name, the rest
# the description. `__blank__` flags it as the "start from scratch" card, which is
# currently hidden (see src/views/Gallery.vue) — the in-app source view is
# read-only, so there's no authoring path; this file stays as the scaffold to copy.
# A real design also sets `__author__` and `__urls__` ({label: url} footer links) —
# outbound links only, since every design already links to its own source at the
# build's commit (see `sourceUrl` in manifest.ts) — and, while it's still being
# worked out, `__experimental__ = True`, which badges its card and header and sorts
# it after the finished designs.
#
# This is the minimal thing the ``Design`` seam accepts: one pydantic parameter and
# a build that turns it into geometry. Copy it, rename ``MyDesign``, and grow it.
# See ``proto.py`` for the full contract and the ``chain`` design for a fleshed-out
# example.
__blank__ = True


class Parameters(BaseModel):
  """Parameters drive the form: each field becomes a control, declared with one of
  ``proto``'s field helpers (``SliderField``, ``SelectField``, …). A field's
  docstring is the help text shown beside it."""

  size: float = SliderField(
    default=20.0,
    name="Size",
    ge=1.0,
    le=100.0,
    slider_min=5,
    slider_max=60,
    step=1,
    unit="mm",
  )
  """Edge length of the box."""


class MyDesign(Design[Parameters]):
  """Instantiating with a ``Parameters`` builds the geometry into ``self``."""

  def __init__(self, params: Parameters):
    self.params = params
    box = Box(params.size, params.size, params.size)
    box.label = "my_design"
    super().__init__(None, label="my_design", children=[box])
