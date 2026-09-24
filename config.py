r"""
config.py — Marvel GPP Path Configuration
==========================================
This file tells the app WHERE your DICOM scan folders live.

Each teammate sets DICOM_ROOT once to their local Google Drive sync path.

Examples:
  Windows (Google Drive):  r"C:\Users\yourname\Google Drive\My Drive\Marvel_GPP_Data"
  Mac (Google Drive):      "/Users/yourname/Google Drive/My Drive/Marvel_GPP_Data"
  Local dev (no Drive):    leave as default — uses the project folder itself

HOW TO SET IT:
  Option A (recommended) — Environment variable, no code change needed:
      Windows:  set DICOM_ROOT=C:\Users\yourname\Google Drive\My Drive\Marvel_GPP_Data
      Mac/Linux: export DICOM_ROOT=/Users/yourname/Google Drive/My Drive/Marvel_GPP_Data

  Option B — Edit this file directly:
      Change the fallback path below to your local Google Drive path.
"""

import os

# --- Edit this line if you are NOT using environment variables ---
_FALLBACK_PATH = os.path.dirname(os.path.abspath(__file__))

# Final DICOM_ROOT — reads from env var first, then falls back above
DICOM_ROOT = os.environ.get("DICOM_ROOT", _FALLBACK_PATH)


def resolve_scan_path(relative_or_absolute: str) -> str:
    """
    Resolves a scan file_path from the database to a full absolute path.

    - If the stored path is already absolute and exists → return as-is (legacy support)
    - If the stored path is relative → join with DICOM_ROOT
    """
    if not relative_or_absolute:
        return ""

    # Already an absolute path that exists on this machine — legacy row, use as-is
    if os.path.isabs(relative_or_absolute) and os.path.exists(relative_or_absolute):
        return relative_or_absolute

    # Relative path — resolve against DICOM_ROOT
    resolved = os.path.join(DICOM_ROOT, relative_or_absolute)
    return resolved


def make_relative_path(absolute_path: str) -> str:
    """
    Converts an absolute DICOM folder path to a path relative to DICOM_ROOT.
    Used when saving new scans to the database.

    e.g. "C:/Users/rithy/.../Marvel_GPP_Data/skull" -> "skull"
    """
    try:
        return os.path.relpath(absolute_path, DICOM_ROOT)
    except ValueError:
        # On Windows, relpath fails if paths are on different drives
        return absolute_path
