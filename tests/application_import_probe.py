"""Run application scenarios in a fresh interpreter without provider adapters."""

from pathlib import Path
import subprocess
import sys


_INSTALL_IMPORT_GUARD = """
from importlib.abc import MetaPathFinder
import sys

BLOCKED = (
    'tabula_rasa.storage', 'tabula_rasa.infrastructure', 'tabula_rasa.composition',
    'tabula_rasa.google_drive', 'tabula_rasa.maps_routing',
    'gspread', 'google', 'streamlit', 'pandas',
)

class RejectInfrastructure(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + '.') for name in BLOCKED):
            raise AssertionError('Application imported infrastructure: ' + fullname)

sys.meta_path.insert(0, RejectInfrastructure())
"""


def assert_application_runs_without_infrastructure(scenario: str) -> None:
    result = subprocess.run(
        [sys.executable, "-c", _INSTALL_IMPORT_GUARD + scenario],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
