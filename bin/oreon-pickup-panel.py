#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import os
import gi
import threading
import logging
import time # For formatting paired time

# Application ID for D-Bus and desktop integration
APP_ID = "com.oreon.Pickup"

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('oreon-pickup-panel')

# Add the parent directory to Python path
try:
    # Get the absolute path to the bin directory
    bin_dir = os.path.dirname(os.path.abspath(__file__))
    # Get the absolute path to the parent directory
    parent_dir = os.path.dirname(bin_dir)
    # Add parent directory to the start of Python path
    if parent_dir not in sys.path:
        sys.path.insert(0, parent_dir)
    
    # Import modules directly
    from oreon-pickup.pickup_config import *
    from oreon_pickup.pickup_net import *
    from oreon_pickup.pickup_state import *
    logger.info(f"Successfully imported Oreon Pickup modules from {parent_dir}")
except ImportError as e:
    logger.error(f"Failed to import Oreon Pickup modules: {str(e)}")
    logger.error(f"Current sys.path: {sys.path}")
    logger.error(f"Looking for modules in: {parent_dir}")
    sys.exit(1)

# Check for Libadwaita (preferred for modern GNOME apps)
try:
    gi.require_version("Adw", "1")
    from gi.repository import Adw
    HAS_ADW = True
except (ValueError, ImportError):
    HAS_ADW = False
    logging.warning("libadwaita (Adw) not found. Falling back to GTK4.")
    logging.warning("Install 'libadwaita' and its gobject-introspection bindings for the intended look and feel.")

# Always require GTK4
try:
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk, Gio, GLib
except (ValueError, ImportError):
    logging.error("GTK 4 not found or python-gobject bindings are missing.")
    logging.error("Please install GTK4 and python3-gobject.")
    sys.exit(1)

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
log = logging.getLogger(__name__)


# Use Adwaita Window if available, otherwise fallback to Gtk Window
BaseWindow = Adw.ApplicationWindow if HAS_ADW else Gtk.ApplicationWindow

class OreonPickupSettingsWindow(BaseWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_default_size(500, 600) # Increased size
        self.set_title("Oreon Pickup Settings")

        self.app = app # Store app reference for later use (e.g., shutdown)
        self._pairing_thread = None
        self._discovery_thread = None
        self._discovered_devices_cache = {} # Store details of discovered devices
        self._main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)

        # Use Adw.ToastOverlay and Adw.HeaderBar if Libadwaita is available
        if HAS_ADW:
            self.main_bin = Adw.ToastOverlay.new()
            self.set_content(self.main_bin) # Use set_content for Adw.ApplicationWindow
            header = Adw.HeaderBar(title_widget=Adw.WindowTitle(title="Oreon Pickup Settings"))
            # Use the main box for content
            self._main_box.set_margin_top(12)
            self._main_box.set_margin_bottom(12)
            self._main_box.set_margin_start(12)
            self._main_box.set_margin_end(12)
            # Add scrolled window for potentially long content
            scrolled_window = Gtk.ScrolledWindow()
            scrolled_window.set_child(self._main_box) # Put main box in scrolled window
            scrolled_window.set_vexpand(True)
            self.main_bin.set_child(scrolled_window) # Add scrolled window to overlay
            self.set_titlebar(header) # Attach header bar directly to window
        else:
            # Fallback for GTK4 only
            header = Gtk.HeaderBar.new()
            header.set_show_title_buttons(True)
            self.set_titlebar(header)
            # Use the main box for content
            self._main_box.set_margin_top(12)
            self._main_box.set_margin_bottom(12)
            self._main_box.set_margin_start(12)
            self._main_box.set_margin_end(12)
            scrolled_window = Gtk.ScrolledWindow()
            scrolled_window.set_child(self._main_box) # Put main box in scrolled window
            scrolled_window.set_vexpand(True)
            self.set_child(scrolled_window) # Add scrolled window to Gtk.ApplicationWindow

        # --- UI Elements ---
        self.pairing_spinner = Gtk.Spinner()
        self.discovery_spinner = Gtk.Spinner()

        # Section for showing this device's code
        self.btn_show_code = Gtk.Button(label="Show My Pairing Code")
        self.btn_show_code.connect("clicked", self.on_show_code_clicked)
        self.pairing_code_display = Gtk.Label(label="", css_classes=['title-1']) # Larger font for code
        self.pairing_code_display.set_selectable(True)

        # Section for entering another device's code
        # Use ComboBoxText to select discovered device
        self.discovered_device_combo = Gtk.ComboBoxText()
        self.discovered_device_combo.set_entry_text_column(0) # Display device name
        self.discovered_device_combo.append_text("Select a discovered device...")
        self.discovered_device_combo.set_active(0) # Select placeholder initially

        self.pairing_code_entry = Gtk.Entry()
        self.pairing_code_entry.set_placeholder_text("Enter 4-digit code")
        self.pairing_code_entry.set_max_length(4)
        self.pairing_code_entry.set_input_purpose(Gtk.InputPurpose.DIGITS)
        self.btn_confirm_code = Gtk.Button(label="Confirm Code")
        self.btn_confirm_code.set_sensitive(False) # Disable until device selected
        self.btn_confirm_code.connect("clicked", self.on_confirm_code_clicked)
        self.discovered_device_combo.connect("changed", self.on_discovered_device_selected)

        # Section for Discovered Devices List
        self.discovered_devices_list_box = Gtk.ListBox()
        self.discovered_devices_list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self.discovered_devices_list_box.set_placeholder(Gtk.Label(label="Searching for devices..."))
        self.discovered_devices_list_box.set_visible(False) # Hide initially until discovery starts

        # Section for Paired Devices List
        self.paired_devices_list_box = Gtk.ListBox()
        self.paired_devices_list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self.paired_devices_list_box.set_placeholder(Gtk.Label(label="No paired devices found."))

        # --- Layout ---
        if HAS_ADW:
            # Use Adwaita Preferences layout
            pair_this_device_group = Adw.PreferencesGroup(title="Pair This Device")
            row_show = Adw.ActionRow(title="Pair This Device")
            row_show.add_suffix(self.btn_show_code)
            row_show.add_suffix(self.pairing_spinner)
            pair_this_device_group.add(row_show)

            row_code_display = Adw.ActionRow(title="Your Code")
            row_code_display.add_suffix(self.pairing_code_display)
            row_code_display.set_activatable_widget(self.pairing_code_display) # Make selectable
            pair_this_device_group.add(row_code_display)
            self.pairing_code_display.set_visible(False) # Hide initially

            pair_other_device_group = Adw.PreferencesGroup(title="Pair With Another Device")
            row_select_device = Adw.ActionRow(title="Discovered Device")
            row_select_device.add_suffix(self.discovered_device_combo)
            row_select_device.add_suffix(self.discovery_spinner) # Show spinner while discovering
            pair_other_device_group.add(row_select_device)

            row_enter_code = Adw.PasswordEntryRow(title="Device Code") if Adw.PasswordEntryRow else Adw.EntryRow(title="Device Code") # Use Password for obscurity
            row_enter_code.set_input_purpose(Gtk.InputPurpose.DIGITS)
            row_enter_code.set_max_length(4)
            self.pairing_code_entry_row = row_enter_code # Store ref
            pair_other_device_group.add(row_enter_code)

            row_confirm = Adw.ActionRow()
            row_confirm.add_suffix(self.btn_confirm_code)
            row_confirm.set_halign(Gtk.Align.CENTER)
            pair_other_device_group.add(row_confirm)

            paired_devices_group = Adw.PreferencesGroup(title="Paired Devices")
            paired_devices_group.add(self.paired_devices_list_box)

            self._main_box.append(pair_this_device_group)
            self._main_box.append(pair_other_device_group)
            self._main_box.append(paired_devices_group)

        else:
            # Simple GtkBox layout
            show_code_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            show_code_box.append(self.btn_show_code)
            show_code_box.append(self.pairing_code_display)
            show_code_box.append(self.pairing_spinner)

            enter_code_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            lbl_select_device = Gtk.Label(label="Discovered Device:")
            discover_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            discover_hbox.append(self.discovered_device_combo)
            discover_hbox.append(self.discovery_spinner)
            lbl_enter_code = Gtk.Label(label="Enter Code:")
            enter_code_box.append(lbl_select_device)
            enter_code_box.append(discover_hbox)
            enter_code_box.append(lbl_enter_code)
            enter_code_box.append(self.pairing_code_entry)
            enter_code_box.append(self.btn_confirm_code)

            lbl_paired = Gtk.Label(label="Paired Devices:")
            lbl_paired.set_halign(Gtk.Align.START)

            self._main_box.append(Gtk.Label(label="Pair This Device", css_classes=['heading']))
            self._main_box.append(show_code_box)
            self._main_box.append(Gtk.Separator())
            self._main_box.append(Gtk.Label(label="Pair With Another Device", css_classes=['heading']))
            self._main_box.append(enter_code_box)
            self._main_box.append(Gtk.Separator())
            self._main_box.append(lbl_paired)
            self._main_box.append(self.paired_devices_list_box)

        # Connect window close event
        self.connect("close-request", self.on_close_request)
        # Connect window show event to start discovery
        self.connect("show", self.on_show)

    def on_close_request(self, *args):
        log.info("Window close requested. Cleaning up network services...")
        self.stop_background_tasks()
        return False # Allow window to close

    def on_show(self, *args):
        log.info("Window shown. Starting discovery and loading state.")
        self.load_paired_devices_ui()
        self.start_discovery_thread()

    def stop_background_tasks(self):
        """Stop any running network threads."""
        # Stop Discovery first
        if self._discovery_thread and self._discovery_thread.is_alive():
            log.info("Stopping discovery thread...")
            stop_discovery() # Signal zeroconf to stop
            # No join needed here, zeroconf runs its own loop management
            self._discovery_thread = None
            self.discovery_spinner.stop()

        # Then stop Pairing
        if self._pairing_thread and self._pairing_thread.is_alive():
            log.info("Stopping pairing service thread...")
            stop_pairing_service()
            self._pairing_thread.join(timeout=1.0) # Wait briefly
        self._pairing_thread = None
        self.pairing_spinner.stop()

    # --- Network Discovery ---

    def start_discovery_thread(self):
        if self._discovery_thread and self._discovery_thread.is_alive():
            log.info("Discovery thread already running.")
            return
        log.info("Starting discovery thread...")
        self.discovery_spinner.start()
        self._discovered_devices_cache = {} # Clear cache
        # Run start_discovery in a separate thread so it doesn't block the UI
        self._discovery_thread = threading.Thread(
            target=start_discovery,
            args=(self.update_discovered_devices_ui,), # Pass the callback
            daemon=True)
        self._discovery_thread.start()

    def update_discovered_devices_ui(self, devices: dict):
        """Callback function executed by GLib.idle_add when discovered devices change."""
        log.debug(f"Updating discovered devices UI: {devices}")
        self.discovery_spinner.stop() # Stop spinner once we get first update
        self._discovered_devices_cache = devices # Update cache

        # Update the ComboBox
        current_selection_id = self.discovered_device_combo.get_active_id()
        self.discovered_device_combo.remove_all()
        self.discovered_device_combo.append("placeholder", "Select a discovered device...")

        paired_ids = set(get_paired_devices().keys())

        for name, info in devices.items():
            hostname = info.get('properties', {}).get('hostname', 'Unknown Host')
            ip = info.get('addresses', ['?'])[0]
            device_id = f"{hostname}@{ip}" # Use same ID format as pairing

            # Don't list already paired devices
            if device_id in paired_ids:
                continue

            display_name = f"{hostname} ({ip})"
            self.discovered_device_combo.append(device_id, display_name)

        # Try to restore previous selection if it still exists
        if current_selection_id and current_selection_id != "placeholder":
            model = self.discovered_device_combo.get_model()
            for i, row in enumerate(model):
                if model.get_value(row.iter, 1) == current_selection_id: # Check ID column (index 1)
                    self.discovered_device_combo.set_active(i)
                    break
            else: # If loop finishes without break
                self.discovered_device_combo.set_active(0) # Reset to placeholder
        else:
            self.discovered_device_combo.set_active(0) # Set placeholder

        self.btn_confirm_code.set_sensitive(self.discovered_device_combo.get_active() > 0)

    def on_discovered_device_selected(self, combo):
        """Enable confirm button only when a real device is selected."""
        is_placeholder = combo.get_active() == 0
        self.btn_confirm_code.set_sensitive(not is_placeholder)


    # --- Pairing Logic ---

    def show_toast(self, message):
        if HAS_ADW and hasattr(self, 'main_bin'):
            self.main_bin.add_toast(Adw.Toast.new(message))

    def pairing_finished(self, result):
        """Callback executed in the main thread after pairing finishes."""
        log.info(f"Pairing finished. Result: {result}")
        self.pairing_spinner.stop()
        self.btn_show_code.set_sensitive(True)
        self.pairing_code_display.set_label(result)
        self.pairing_code_display.set_visible(True)
        self.load_paired_devices_ui() # Refresh paired list in case pairing succeeded

    def on_show_code_clicked(self, button):
        log.info("Show Code button clicked.")
        self.stop_background_tasks() # Stop any previous network activity
        self.start_discovery_thread() # Restart discovery if stopped
        code = generate_pairing_code()
        self.pairing_code_display.set_label(code)
        self.pairing_code_display.set_visible(True)

    def on_confirm_code_clicked(self, button):
        log.info("Confirm Code button clicked.")

        # Get selected device ID from ComboBox
        selected_id = self.discovered_device_combo.get_active_id()
        if not selected_id or selected_id == "placeholder":
            self.show_toast("Please select a discovered device.")
            return

        # Find the selected device details from cache
        selected_device_info = self._discovered_devices_cache.get(selected_id)
        # The ID we store in the combo is hostname@ip, but cache key might be zeroconf name
        # We need a robust way to get IP/Port from the selected ID
        # For now, assume ID is 'hostname@ip' and extract IP
        try:
            target_ip = selected_id.split('@')[1]
            target_port = DEFAULT_PORT # Assume default
        except IndexError:
            log.error(f"Could not parse IP from selected device ID: {selected_id}")
            self.show_toast("Error getting device details.")
            return

        # Get code from UI elements (adapt for Adwaita rows)
        if HAS_ADW:
            code = self.pairing_code_entry_row.get_text()
        else:
            code = self.pairing_code_entry.get_text()

        if not code or not code.isdigit() or len(code) != 4:
            self.show_toast("Please enter a valid 4-digit code.")
            return

        self.stop_background_tasks() # Ensure no other network tasks interfere
        self.start_discovery_thread() # Restart discovery
        self.pairing_spinner.start()
        self.btn_confirm_code.set_sensitive(False)

        # Run pairing initiation in background thread
        self._pairing_thread = threading.Thread(
            target=self._run_initiate_pairing,
            args=(target_ip, target_port, code),
            daemon=True)
        self._pairing_thread.start()

    def _run_initiate_pairing(self, ip, port, code):
        try:
            success = initiate_pairing(ip, port, code)
            if success:
                GLib.idle_add(self.pairing_succeeded)
            else:
                GLib.idle_add(self.pairing_finished, "Pairing Failed.")
        except Exception as e:
            log.error(f"Error initiating pairing: {e}", exc_info=True)
            GLib.idle_add(self.pairing_finished, f"Pairing Error: {e}")
        finally:
            GLib.idle_add(self.pairing_spinner.stop)

    def pairing_succeeded(self):
        """Callback executed in the main thread after successful pairing."""
        self.pairing_finished("Pairing Successful!")
        # Reset input fields
        if HAS_ADW:
            self.pairing_code_entry_row.set_text("")
        else:
            self.pairing_code_entry.set_text("")
        self.discovered_device_combo.set_active(0) # Reset selection
        # No need to explicitly refresh paired list here, pairing_finished does it

    # --- Paired Devices List ---

    def load_paired_devices_ui(self):
        """Loads paired devices from state and updates the list UI."""
        log.debug("Loading paired devices UI.")
        # Clear existing rows from list box
        # Iterate backwards to avoid issues while removing
        for i in range(len(self.paired_devices_list_box) - 1, -1, -1):
            row = self.paired_devices_list_box.get_row_at_index(i)
            self.paired_devices_list_box.remove(row)

        paired_devices = get_paired_devices()
        log.debug(f"Found paired devices: {paired_devices}")

        if not paired_devices:
            self.paired_devices_list_box.set_placeholder(Gtk.Label(label="No paired devices found."))
            return

        for device_id, info in paired_devices.items():
            hostname = info.get('hostname', 'Unknown Host')
            ip = info.get('ip', 'N/A')
            paired_timestamp = info.get('paired_at')
            paired_time_str = f"Paired: {time.strftime('%Y-%m-%d %H:%M', time.localtime(paired_timestamp))}" if paired_timestamp else ""

            row = Adw.ActionRow(title=hostname, subtitle=f"{ip} - {paired_time_str}") if HAS_ADW else Gtk.ListBoxRow()

            unpair_button = Gtk.Button(icon_name="edit-delete-symbolic", tooltip_text="Unpair this device")
            # Pass device_id to the handler using a lambda
            unpair_button.connect("clicked", lambda btn, d_id=device_id: self.on_unpair_clicked(d_id))

            if HAS_ADW:
                row.add_suffix(unpair_button)
            else:
                # For plain Gtk.ListBoxRow, add a Box containing label and button
                hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                label = Gtk.Label(label=f"{hostname} ({ip}) - {paired_time_str}", halign=Gtk.Align.START, hexpand=True)
                hbox.append(label)
                hbox.append(unpair_button)
                row.set_child(hbox)

            self.paired_devices_list_box.append(row)

    def on_unpair_clicked(self, device_id):
        log.info(f"Unpair clicked for device: {device_id}")
        remove_paired_device(device_id)
        self.show_toast(f"Device {device_id.split('@')[0]} unpaired.")
        self.load_paired_devices_ui() # Refresh the list


# Use Adwaita Application if available, otherwise fallback to Gtk Application
class OreonPickupSettingsApp(Adw.Application if HAS_ADW else Gtk.Application):
    def __init__(self, **kwargs):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE, **kwargs)
        self.connect("activate", self.on_activate)

    def on_activate(self, app):
        self.win = OreonPickupSettingsWindow(self)
        self.win.present()


if __name__ == "__main__":
    # Initialize Adwaita if available
    if HAS_ADW:
        Adw.init()

    app = OreonPickupSettingsApp()
    exit_status = app.run(sys.argv)
    sys.exit(exit_status) 