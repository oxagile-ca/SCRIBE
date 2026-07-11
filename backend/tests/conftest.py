"""Make the backend package importable from tests/ without installing it."""
import os
import sys
import tempfile

# Isolate the test suite from the PRODUCTION pipeline DB
# (~/qa-dashboard/pipeline-state.db). Several tests exercise the live
# pipeline_store; without this they wrote phantom PROJ-* rows into the real board
# DB (some via async council writes that outlive the test). Point PIPELINE_DB at a
# throwaway temp file BEFORE any test imports config/server. Must run at import
# time — config.PIPELINE_DB is read once at module load.
_TEST_PIPELINE_DB = os.path.join(tempfile.gettempdir(), "scribe-test-pipeline-state.db")
if "SCRIBE_PIPELINE_DB" not in os.environ:
    try:
        if os.path.exists(_TEST_PIPELINE_DB):
            os.remove(_TEST_PIPELINE_DB)
    except OSError:
        pass
    os.environ["SCRIBE_PIPELINE_DB"] = _TEST_PIPELINE_DB

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)
