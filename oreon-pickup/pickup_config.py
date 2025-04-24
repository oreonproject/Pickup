# -*- coding: utf-8 -*-

from pathlib import Path

VERSION = "0.1.0"
APP_ID_CLI = "org.oreon.PickupCLI"  # Base ID for CLI tool
APP_ID_SETTINGS = "org.oreon.PickupSettings" # ID for the Settings Panel UI

# State Storage
STATE_DIR = Path.home() / ".local" / "share" / "oreon-pickup"
STATE_FILE = STATE_DIR / "state.json"

# Networking
SERVICE_TYPE = "_oreon-pickup._tcp.local."
DEFAULT_PORT = 50309 # Example port, should be registered or configurable
PAIRING_TIMEOUT = 60 # Seconds to wait for pairing confirmation

# GNOME Control Center Integration
PANEL_EXEC_PATH = "/usr/bin/oreon-pickup-panel"
PANEL_DATA_DIR = "/usr/share/gnome-control-center/panels"
APP_DATA_DIR = "/usr/share/applications"

# Add more configuration variables as needed 