"""
State management for Oreon Pickup
"""

import os
import json
import time
from .pickup_config import STATE_FILE

def get_state_file_path():
    """Get the absolute path to the state file"""
    return os.path.join(os.path.expanduser("~"), ".config", "oreon-pickup", STATE_FILE)

def load_state():
    """Load the current state from file"""
    state_file = get_state_file_path()
    if not os.path.exists(state_file):
        return {"paired_devices": {}}
    
    try:
        with open(state_file, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"Error loading state: {e}")
        return {"paired_devices": {}}

def write_state(state):
    """Write the state to file"""
    state_file = get_state_file_path()
    os.makedirs(os.path.dirname(state_file), exist_ok=True)
    
    try:
        with open(state_file, 'w') as f:
            json.dump(state, f, indent=2)
    except IOError as e:
        print(f"Error writing state: {e}")

def get_paired_devices():
    """Get the list of paired devices"""
    state = load_state()
    return state.get("paired_devices", {})

def add_paired_device(device_id, hostname, ip):
    """Add a new paired device"""
    state = load_state()
    state["paired_devices"][device_id] = {
        "hostname": hostname,
        "ip": ip,
        "paired_at": time.time()
    }
    write_state(state)

def remove_paired_device(device_id):
    """Remove a paired device"""
    state = load_state()
    if device_id in state["paired_devices"]:
        del state["paired_devices"][device_id]
        write_state(state) 