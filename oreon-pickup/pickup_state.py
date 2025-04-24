# -*- coding: utf-8 -*-

import json
import logging
from typing import Dict, Optional

from . import pickup_config

log = logging.getLogger(__name__)

DEFAULT_STATE = {
    "version": 1,
    "schema_version": pickup_config.VERSION,
    "applications": [],
    "files": [],
    "notifications": [],
    "paired_devices": {}
}

def load_state() -> Dict:
    """Loads the state from the JSON file, returning default if not found or invalid."""
    if not pickup_config.STATE_FILE.exists():
        log.info(f"State file not found at {pickup_config.STATE_FILE}. Returning default state.")
        return DEFAULT_STATE.copy()
    try:
        with open(pickup_config.STATE_FILE, "r") as f:
            state_data = json.load(f)
            # Basic validation and merging with defaults for missing keys
            validated_state = DEFAULT_STATE.copy()
            validated_state.update(state_data) # Overwrite defaults with loaded data
            # Ensure essential keys exist even if loaded file was minimal
            for key, default_value in DEFAULT_STATE.items():
                if key not in validated_state:
                    validated_state[key] = default_value
            return validated_state
    except (IOError, json.JSONDecodeError) as e:
        log.error(f"Failed to read or parse state file {pickup_config.STATE_FILE}: {e}. Returning default state.")
        return DEFAULT_STATE.copy() # Return default empty state on error
    except Exception as e:
        log.error(f"An unexpected error occurred reading state file: {e}", exc_info=True)
        return DEFAULT_STATE.copy()

def write_state(state_data: Dict):
    """Writes the given state data to the JSON file."""
    try:
        pickup_config.STATE_DIR.mkdir(parents=True, exist_ok=True)
        with open(pickup_config.STATE_FILE, "w") as f:
            json.dump(state_data, f, indent=2)
        log.debug(f"State written successfully to {pickup_config.STATE_FILE}")
    except IOError as e:
        log.error(f"Failed to write state file {pickup_config.STATE_FILE}: {e}")
    except Exception as e:
        log.error(f"An unexpected error occurred during state write: {e}", exc_info=True)

def get_paired_devices() -> Dict:
    """Returns the dictionary of paired devices from the state file."""
    state = load_state()
    return state.get("paired_devices", {})

def add_paired_device(device_id: str, device_info: Dict):
    """Adds or updates a paired device in the state file."""
    if not device_id or not isinstance(device_info, dict):
        log.warning(f"Attempted to add invalid paired device: id={device_id}, info={device_info}")
        return
    state = load_state()
    # Ensure the key exists
    if "paired_devices" not in state:
        state["paired_devices"] = {}
    state["paired_devices"][device_id] = device_info
    log.info(f"Adding/updating paired device: {device_id} -> {device_info}")
    write_state(state)

def remove_paired_device(device_id: str):
    """Removes a paired device from the state file."""
    state = load_state()
    if "paired_devices" in state and device_id in state["paired_devices"]:
        log.info(f"Removing paired device: {device_id}")
        del state["paired_devices"][device_id]
        write_state(state)
    else:
        log.warning(f"Attempted to remove non-existent paired device: {device_id}") 