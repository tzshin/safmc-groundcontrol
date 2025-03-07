"""
Configuration and constants for the ESPKenisis Radio Link Manager.
"""

import PySimpleGUI as sg
import os

# Environment settings
os.environ["XDG_SESSION_TYPE"] = "xcb"

# Global UI scaling parameter
UI_SCALE = 2.4  # Adjust this value to change UI scaling

# Apply scaling settings
sg.set_options(
    scaling=UI_SCALE,
    font=("Helvetica", 10),
)

# Constants
WINDOW_TITLE = "ESPKenisis Radio Link Manager"
REFRESH_RATE_MS = 100
THEME = "DarkBlue14"
DEBUG = True  # Set to True to enable debug prints


def debug_print(*args, **kwargs):
    """Print debug messages if DEBUG is enabled"""
    if DEBUG:
        print("[DEBUG]", *args, **kwargs)
