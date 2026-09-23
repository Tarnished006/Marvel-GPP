"""
Pytest configuration for Aegis-Touch / Marvel-GPP test suite.
Ensures repository root is always on sys.path and is the working directory.
"""
import os
import sys

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)
os.chdir(ROOT_DIR)
