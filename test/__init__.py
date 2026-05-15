"""Test configuration. Run with: PYTHONPATH=src python3 -m unittest discover test -v

Note: ``unittest discover`` does NOT execute this file before loading
test modules when the start directory is added directly to sys.path.
Set ``PYTHONPATH=src`` in the environment for reliable imports.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
