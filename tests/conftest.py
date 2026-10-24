import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BANKS = os.path.join(REPO_ROOT, "banks")
SCENARIOS = os.path.join(REPO_ROOT, "scenarios")
