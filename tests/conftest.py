"""Keep test jobs out of the shared dev job directory."""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile

_ROOT = tempfile.mkdtemp(prefix="pdfconv-tests-")
os.environ["PDFCONV_JOB_DIR"] = _ROOT
atexit.register(shutil.rmtree, _ROOT, True)
