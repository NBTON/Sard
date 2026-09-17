"""F-5: import-time .env loading must honor SARD_DISABLE_DOTENV.

The import is exercised in a subprocess whose cwd contains a sentinel .env,
so no in-process module state or cwd leaks between tests. With the isolation
flag set the sentinel must stay out of the environment; with default
behavior the load still happens.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SENTINEL = "SARD_F5_TEST_SENTINEL_XYZ"

REPO_ROOT = Path(__file__).resolve().parents[1]

_PROBE = (
    "import os, sys; "
    "sys.path.insert(0, {root!r}); "
    "import sard.agent.tools.cultural_tools; "
    "import sard.agent.tools.multimodal_tools; "
    "import sard.cli.demo; "
    "import sard.cli.rag; "
    "import sard.config.models; "
    "print('LEAK' if {sentinel!r} in os.environ else 'CLEAN')"
).format(root=str(REPO_ROOT), sentinel=SENTINEL)


def _probe(tmp_path, extra_env):
    (tmp_path / ".env").write_text("%s=sentinel-value\n" % SENTINEL, encoding="utf-8")
    env = dict(os.environ)
    env.pop(SENTINEL, None)
    # PYTEST_CURRENT_TEST is a pytest-phase marker, not a product contract:
    # drop it so the probe measures only the SARD_DISABLE_DOTENV flag.
    env.pop("PYTEST_CURRENT_TEST", None)
    env.update(extra_env)
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE],
        capture_output=True, text=True, cwd=str(tmp_path), env=env, timeout=180,
    )
    assert proc.returncode == 0, proc.stderr[-500:]
    return proc.stdout.strip().splitlines()[-1]


def test_dotenv_isolation_flag_blocks_import_time_load(tmp_path):
    assert _probe(tmp_path, {"SARD_DISABLE_DOTENV": "1"}) == "CLEAN"


def test_dotenv_default_behavior_still_loads(tmp_path):
    env = {"SARD_DISABLE_DOTENV": "0"}
    assert _probe(tmp_path, env) == "LEAK"
