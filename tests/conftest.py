"""Gör de rena modulerna importerbara utan Home Assistant.

Paketets __init__.py importerar homeassistant, så varje syskonmodul drar in
HA. Finns HA installerat används det riktiga paketet; annars (CI) ersätts
homeassistant.* av tomma moduler vars attribut är MagicMock.
"""
import importlib.abc
import importlib.machinery
import os
import sys
import time
import types
from unittest.mock import MagicMock

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# Planeraren slår upp lastprofilens timme i värdens lokala zon
# (dt.astimezone()), och backtestdatan har UTC-stämplar. I HA är processens
# zon husets; CI kör UTC och fick därför ett 2 h förskjutet resultat.
# Windows saknar tzset men kör redan i husets zon lokalt.
if hasattr(time, "tzset"):
    os.environ["TZ"] = "Europe/Stockholm"
    time.tzset()


class _HAStubFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, name, path=None, target=None):
        if name == "homeassistant" or name.startswith("homeassistant."):
            return importlib.machinery.ModuleSpec(name, self, is_package=True)
        return None

    def create_module(self, spec):
        module = types.ModuleType(spec.name)
        module.__path__ = []
        module.__getattr__ = lambda attr: MagicMock(name=f"{spec.name}.{attr}")
        return module

    def exec_module(self, module):
        pass


try:
    import homeassistant  # noqa: F401
except ImportError:
    sys.meta_path.insert(0, _HAStubFinder())
