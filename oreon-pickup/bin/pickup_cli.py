#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import os
import subprocess
import sys
import time
import logging

# Add the parent directory to Python path
try:
    # Get the absolute path to the bin directory
    bin_dir = os.path.dirname(os.path.abspath(__file__))
    # Get the absolute path to the parent directory
    parent_dir = os.path.dirname(bin_dir)
    # Add parent directory to the start of Python path
    if parent_dir not in sys.path:
        sys.path.insert(0, parent_dir)
    
    # Now try to import our modules
    from oreon_pickup import pickup_config, pickup_net, pickup_state
    logging.info(f"CLI: Successfully imported Oreon Pickup modules from {parent_dir}")
except ImportError as e:
    logging.error(f"CLI: Failed to import Oreon Pickup modules: {str(e)}")
    # Print sys.path for debugging
    # logging.error(f"CLI: Current sys.path: {sys.path}")
    # logging.error(f"CLI: Looking for modules in: {parent_dir}")
    # Provide a more user-friendly error message
    print(f"Error: Could not import necessary Oreon Pickup modules ({e}).", file=sys.stderr)
    print("       Ensure the script is run from the correct directory and required dependencies are installed.", file=sys.stderr)
    sys.exit(1)

# Requires python-gobject (check after own modules are imported)
try:
    import gi
    # Try importing Gtk 4 first, fallback to 3 if needed for broader compatibility initially
    try:
         gi.require_version('Gtk', '4.0')
    except ValueError:
         try:
             gi.require_version('Gtk', '3.0')
         except ValueError:
             print("Error: GTK 3.0 or 4.0 not found.", file=sys.stderr)
             # Continue without GTK for CLI, but log warning
             logging.warning("GTK 3.0 or 4.0 not found, some features might be limited.")
    gi.require_version('Gio', '2.0')
    from gi.repository import Gio, GLib
    HAS_GIO = True
except ImportError:
    print("Warning: python-gobject (Gio/GLib) not found. Application restore functionality will be disabled.", file=sys.stderr)
    print("         Install it using: 'sudo dnf install python3-gobject'", file=sys.stderr)
    HAS_GIO = False

# Setup logging (can be configured further)
# Moved logger setup after initial imports to avoid potential issues
log = logging.getLogger('oreon-pickup-cli')
log.setLevel(logging.INFO) # Set default level
# Add handler if not already configured by basicConfig in panel
if not logging.getLogger().handlers:
     handler = logging.StreamHandler(sys.stderr)
     formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
     handler.setFormatter(formatter)
     logging.getLogger().addHandler(handler)

def get_running_app_ids():
    """
    Attempts to get a list of running application IDs (.desktop IDs).

    This is complex and environment-dependent, especially under Wayland.
    This implementation uses a gdbus call to Shell Eval as a primary method,
    which might be restricted or change in different GNOME versions.
    """
    app_ids = set()
    log.info("Attempting to detect running applications via GNOME Shell Eval...")
    try:
        # Note: org.gnome.Shell.Eval might be restricted.
        command = [
            "gdbus", "call", "--session", "--dest", "org.gnome.Shell",
            "--object-path", "/org/gnome/Shell", "--method", "org.gnome.Shell.Eval",
            'global.get_window_actors().map(a => a.get_meta_window()?.get_gtk_application_id() || null).filter(id => id)'
        ]
        result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=5)

        output = result.stdout.strip()

        # Output is like: (true, '["app.id.1", "app.id.2"]')
        if output.startswith("(true, '") and output.endswith("')"):
            json_str = output[len("(true, '"):-len("')")]
            json_str = json_str.replace("\\"", "\\") # Handle escaped backslashes if any
            json_str = json_str.replace("\"", """) # Handle escaped quotes
            try:
                parsed_list = json.loads(json_str)
                app_ids.update(item for item in parsed_list if isinstance(item, str) and item)
                log.info(f"Detected via Eval: {app_ids}")
            except json.JSONDecodeError as e:
                log.warning(f"Could not parse Shell Eval output: {e}. Output fragment: '{json_str[:100]}...'")
        else:
            log.warning(f"Unexpected Shell Eval output format: {output[:100]}...")

    except subprocess.CalledProcessError as e:
        log.warning(f"'gdbus call org.gnome.Shell.Eval' failed (command: '{' '.join(e.cmd)}'). It might be disabled or the interface changed.")
        # log.warning(f"  Stderr: {e.stderr}") # Can be noisy
    except FileNotFoundError:
        log.warning("'gdbus' command not found. Cannot query GNOME Shell.")
    except subprocess.TimeoutExpired:
        log.warning("'gdbus call org.gnome.Shell.Eval' timed out.")
    except Exception as e:
        log.error(f"An unexpected error occurred querying GNOME Shell: {e}", exc_info=True)

    if not app_ids:
         log.warning("Could not reliably detect running applications. State might be incomplete.")

    return sorted(list(app_ids))

def save_state():
    """Saves the current state (apps, files, notifications) to a JSON file."""
    log.info("Saving state...")
    current_state = pickup_state.load_state() # Load existing state to preserve paired devices etc.

    app_ids = get_running_app_ids()

    current_state.update({
        # Update fields managed by save_state, keep others (like paired_devices)
        "version": pickup_state.DEFAULT_STATE["version"],
        "schema_version": pickup_config.VERSION,
        "applications": app_ids,
        "files": [],  # Placeholder for future implementation
        "notifications": [], # Placeholder for future implementation
    })

    pickup_state.write_state(current_state)

    if app_ids:
        log.info(f"  Applications recorded: {len(app_ids)}")
    else:
        log.info("  No applications recorded (detection might have failed).")

def restore_state():
    """Restores the state (primarily applications) from the saved JSON file."""
    log.info("Restoring state...")
    if not HAS_GIO:
         log.error("Cannot restore state: Gio/GLib (python-gobject) is not available.")
         return

    state_data = pickup_state.load_state()
    if not state_data or not state_data.get("applications"): # Check if state exists and has apps
        log.info("No application state found to restore.")
        # Proceed to restore files/notifications if implemented later
        return

    app_ids_to_restore = state_data.get("applications", [])

    log.info(f"Attempting to restore {len(app_ids_to_restore)} applications...")
    activated_count = 0
    failed_apps = []

    app_launch_context = Gio.AppLaunchContext.new() # Use Gio.AppLaunchContext.new()

    for app_id in app_ids_to_restore:
        if not isinstance(app_id, str) or not app_id:
            log.warning(f"  Skipping invalid application ID: {app_id}")
            continue

        app_info = Gio.AppInfo.get_for_id(app_id)
        if not app_info:
            log.warning(f"  Could not find application info for '{app_id}'. Skipping.")
            failed_apps.append(f"{app_id} (not found)")
            continue

        log.info(f"  Launching '{app_info.get_display_name()}' ({app_id})...")
        try:
            app_info.launch([], app_launch_context)
            activated_count += 1
            # time.sleep(0.2) # Optional delay?
        except GLib.Error as e:
            log.error(f"  Error launching {app_id}: {e}")
            failed_apps.append(f"{app_id} (launch error: {e.message})")
        except Exception as e:
             log.error(f"  An unexpected error occurred launching {app_id}: {e}", exc_info=True)
             failed_apps.append(f"{app_id} (unexpected error)")

    log.info("Restore summary:")
    log.info(f"  Successfully launched: {activated_count}")
    if failed_apps:
        log.warning(f"  Failed to launch: {len(failed_apps)}")
        for app in failed_apps:
            log.warning(f"    - {app}")

    # --- Placeholder sections for future features ---
    files_to_restore = state_data.get("files", [])
    if files_to_restore:
        log.info("Restoring files (Not Implemented Yet)...")

    notifications_to_restore = state_data.get("notifications", [])
    if notifications_to_restore:
        log.info("Restoring notifications (Not Implemented Yet)...")

def cli_discover(args):
    """Handle the 'discover' CLI command."""
    log.info("Starting network discovery...")
    # Define a simple callback for the CLI
    discovered_devices_cli = {}
    def cli_update_callback(devices):
        nonlocal discovered_devices_cli
        discovered_devices_cli = devices
        # Simple display update - could be fancier
        sys.stdout.write("\r" + f"Found: {len(devices)} devices. {devices}")
        sys.stdout.flush()

    if not pickup_net.start_discovery(update_callback=cli_update_callback):
        log.error("Failed to start discovery. Is zeroconf installed?")
        return

    print(f"Discovering Oreon Pickup devices for {args.timeout} seconds... Press Ctrl+C to stop early.")
    try:
        time.sleep(args.timeout)
        print("\nDiscovery finished.")
        if discovered_devices_cli:
            print("Discovered Devices:")
            for name, info in discovered_devices_cli.items():
                # Extract info more carefully
                hostname = info.properties.get(b'hostname', b'Unknown').decode()
                server = info.server if info.server else '?'
                port = info.port if info.port else '?'
                addresses = [socket.inet_ntoa(addr) for addr in info.addresses] if info.addresses else []
                print(f"  - {hostname} ({server}:{port}) @ {addresses}")
        else:
            print("No devices found.")

    except KeyboardInterrupt:
        print("\nDiscovery interrupted by user.")
    finally:
        pickup_net.stop_discovery()
        print("Discovery stopped.")

def cli_pair_show_code(args):
    """Handle the 'pair show-code' CLI command."""
    code = pickup_net.generate_pairing_code()
    print(f"Generated pairing code: {code}")
    print(f"Waiting for connection on port {pickup_config.DEFAULT_PORT} for {pickup_config.PAIRING_TIMEOUT} seconds...")
    pickup_net.advertise_service() # Advertise while waiting
    pickup_net.start_pairing_service(code)
    # Keep the main thread alive while the pairing server runs in background
    # We rely on the server's timeout or user interrupt (Ctrl+C)
    try:
        # Access the server thread (might be fragile if structure changes)
        server_thread = pickup_net._pairing_server_thread
        if server_thread:
            # Join the thread to wait for it to finish (or timeout)
            server_thread.join(timeout=pickup_config.PAIRING_TIMEOUT + 5) # Wait slightly longer than timeout
        else:
             log.error("Could not find pairing server thread.")
             # If thread creation failed, just wait for timeout duration
             time.sleep(pickup_config.PAIRING_TIMEOUT)
    except KeyboardInterrupt:
        print("\nPairing interrupted by user.")
    finally:
        pickup_net.stop_pairing_service()
        pickup_net.stop_advertising()
        print("Pairing service stopped.")

def cli_pair_enter_code(args):
    """Handle the 'pair enter-code' CLI command."""
    if not args.ip or not args.code:
        log.error("Both IP address and code are required.")
        parser.print_help()
        return
    print(f"Attempting to pair with {args.ip} using code {args.code}...")
    success = pickup_net.initiate_pairing(args.ip, args.port, args.code)
    if success:
        print("Pairing successful!")
        # State is saved within initiate_pairing now
    else:
        print("Pairing failed.")

def cli_list_paired(args):
    """Handle the 'list-paired' CLI command."""
    paired_devices = pickup_state.get_paired_devices()
    if not paired_devices:
        print("No devices paired yet.")
        return
    print("Paired Devices:")
    for device_id, info in paired_devices.items():
        # Use hostname if available, otherwise device_id
        name = info.get('hostname', device_id)
        ip = info.get('ip', 'N/A')
        paired_at = info.get('paired_at')
        time_str = f"on {time.strftime('%Y-%m-%d %H:%M', time.localtime(paired_at))}" if paired_at else ""
        print(f"  - {name} ({ip}) [ID: {device_id}] {time_str}")

def main():
    parser = argparse.ArgumentParser(
        description=f"Oreon Pickup CLI v{pickup_config.VERSION}: Save, restore, and sync session state.",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    # Use version from __init__ if available, otherwise config
    try:
         from oreon_pickup import __version__ as pkg_version
    except ImportError:
         pkg_version = pickup_config.VERSION
    parser.add_argument('--version', action='version', version=f'%(prog)s {pkg_version}')
    parser.add_argument('-v', '--verbose', action='store_true', help='Enable verbose logging')

    subparsers = parser.add_subparsers(dest='command', help='Available commands', required=True)

    # Save command
    parser_save = subparsers.add_parser('save', help='Save the current session state')
    parser_save.set_defaults(func=lambda args: save_state())

    # Restore command
    parser_restore = subparsers.add_parser('restore', help='Restore the last saved session state')
    parser_restore.set_defaults(func=lambda args: restore_state())

    # Discover command
    parser_discover = subparsers.add_parser('discover', help='Discover other Oreon Pickup devices on the network')
    parser_discover.add_argument('-t', '--timeout', type=int, default=10, help='Duration in seconds to listen for devices')
    parser_discover.set_defaults(func=cli_discover)

    # Pair command group
    parser_pair = subparsers.add_parser('pair', help='Manage device pairing')
    pair_subparsers = parser_pair.add_subparsers(dest='pair_command', help='Pairing actions', required=True)

    # Pair show-code
    parser_pair_show = pair_subparsers.add_parser('show-code', help='Generate a code and wait for another device to connect')
    parser_pair_show.set_defaults(func=cli_pair_show_code)

    # Pair enter-code
    parser_pair_enter = pair_subparsers.add_parser('enter-code', help='Connect to another device using its IP and code')
    parser_pair_enter.add_argument('ip', help='IP address of the device showing the code')
    parser_pair_enter.add_argument('code', help='The 4-digit code shown on the other device')
    parser_pair_enter.add_argument('-p', '--port', type=int, default=pickup_config.DEFAULT_PORT, help='Port number of the device showing the code')
    parser_pair_enter.set_defaults(func=cli_pair_enter_code)

    # Pair list command
    parser_pair_list = pair_subparsers.add_parser('list', help='List currently paired devices')
    parser_pair_list.set_defaults(func=cli_list_paired)

    # --- Add future commands: send, listen, unpair --- #

    args = parser.parse_args()

    # Set log level based on verbosity
    if args.verbose:
        log.setLevel(logging.DEBUG)
        logging.getLogger('oreon_pickup').setLevel(logging.DEBUG) # Set for package modules too
    else:
        log.setLevel(logging.INFO)
        logging.getLogger('oreon_pickup').setLevel(logging.INFO)

    if hasattr(args, 'func'):
        try:
            args.func(args)
        except Exception as e:
            log.error(f"An unexpected error occurred: {e}", exc_info=True)
            sys.exit(1)
    else:
        parser.print_help()
        sys.exit(1)

if __name__ == "__main__":
    main() 