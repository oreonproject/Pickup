# -*- coding: utf-8 -*-

import socket
import select
import threading
import json
import time
import random
import sys
import logging
from typing import Dict, Optional, List

# Attempt to import zeroconf
try:
    from zeroconf import ServiceInfo, Zeroconf, ServiceBrowser, ServiceStateChange
    HAS_ZEROCONF = True
except ImportError:
    HAS_ZEROCONF = False
    print("Warning: 'zeroconf' library not found. Network discovery will not work.", file=sys.stderr)
    print("         Install using: 'pip install zeroconf' or find your distribution's package.", file=sys.stderr)

from . import pickup_config
from . import pickup_state # Import the state management module

# Attempt to import GLib
try:
    from gi.repository import GLib # For thread-safe UI updates
    HAS_GLIB = True
except ImportError:
    HAS_GLIB = False

# Basic logging setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class DeviceDiscoveryListener:
    """Handles zeroconf service discovery events and notifies a listener."""
    def __init__(self, update_callback=None):
        self.discovered_devices = {}
        self.update_callback = update_callback

    def _notify_update(self):
        """Calls the callback with the current device list, ensuring it runs in the main GLib loop."""
        if self.update_callback:
            devices_copy = self.get_devices() # Get a copy
            if HAS_GLIB:
                GLib.idle_add(self.update_callback, devices_copy)
            else:
                # Fallback if GLib is not available (e.g., CLI usage)
                # This might cause issues if the callback directly modifies UI
                try:
                    self.update_callback(devices_copy)
                except Exception as e:
                     logging.error(f"Error in discovery update callback (non-GLib): {e}")

    def remove_service(self, zeroconf_instance, type, name):
        logging.info(f"Service {name} removed")
        if name in self.discovered_devices:
            del self.discovered_devices[name]
            self._notify_update()

    def add_service(self, zeroconf_instance, type, name):
        info = zeroconf_instance.get_service_info(type, name)
        if info:
            addresses = [socket.inet_ntoa(addr) for addr in info.addresses]
            logging.info(f"Service {name} added, service info: {info}")
            self.discovered_devices[name] = {
                "name": info.name,
                "server": info.server,
                "port": info.port,
                "addresses": addresses,
                "properties": {k.decode(): v.decode() for k, v in info.properties.items()},
            }
            self._notify_update()
        else:
            logging.warning(f"Could not get info for service {name}")

    def update_service(self, zeroconf_instance, type, name):
        # Often same as add_service logic for our purposes
        logging.info(f"Service {name} updated")
        self.add_service(zeroconf_instance, type, name)

    def get_devices(self) -> Dict:
        return self.discovered_devices.copy()

# Global variables for discovery (consider encapsulating in a class)
zeroconf_instance: Optional[Zeroconf] = None
discovery_browser: Optional[ServiceBrowser] = None
discovery_listener: Optional[DeviceDiscoveryListener] = None
service_info: Optional[ServiceInfo] = None

def start_discovery(update_callback=None) -> bool:
    """Starts listening for other Oreon Pickup devices on the network.

    Args:
        update_callback: A function to call when the list of discovered devices changes.
                         The function will receive a dictionary of devices as an argument.
                         It will be called via GLib.idle_add for thread safety if GLib is available.
    """
    global zeroconf_instance, discovery_browser, discovery_listener
    if not HAS_ZEROCONF:
        logging.error("Zeroconf library not available. Cannot start discovery.")
        return False
    if discovery_browser:
        logging.warning("Discovery already running.")
        return True # Or False depending on desired strictness

    logging.info(f"Starting mDNS discovery for type '{pickup_config.SERVICE_TYPE}'")
    if not HAS_GLIB and update_callback:
        logging.warning("GLib not found; UI update callbacks from discovery may not be thread-safe.")

    try:
        # Ensure previous instance is closed if any step fails
        if zeroconf_instance:
            zeroconf_instance.close()
        zeroconf_instance = Zeroconf()
        discovery_listener = DeviceDiscoveryListener(update_callback=update_callback)
        discovery_browser = ServiceBrowser(zeroconf_instance, pickup_config.SERVICE_TYPE, listener=discovery_listener)
        logging.info("Service browser started.")
        return True
    except Exception as e:
        logging.error(f"Failed to start Zeroconf discovery: {e}")
        if zeroconf_instance:
            zeroconf_instance.close()
            zeroconf_instance = None
        discovery_browser = None
        discovery_listener = None
        return False

def stop_discovery():
    """Stops the mDNS discovery process."""
    global zeroconf_instance, discovery_browser, discovery_listener
    if not HAS_ZEROCONF or not zeroconf_instance:
        logging.info("Zeroconf not running or not available.")
        return

    logging.info("Stopping mDNS discovery...")
    if discovery_browser:
        discovery_browser.cancel() # Request stop
        discovery_browser = None
    if zeroconf_instance:
        zeroconf_instance.close()
        zeroconf_instance = None
    discovery_listener = None
    logging.info("Discovery stopped.")

def get_discovered_devices() -> Dict:
    """Returns a copy of the currently discovered devices."""
    if discovery_listener:
        return discovery_listener.get_devices()
    return {}

def advertise_service(port: int = pickup_config.DEFAULT_PORT) -> bool:
    """Advertises this device as an Oreon Pickup service."""
    global zeroconf_instance, service_info
    if not HAS_ZEROCONF:
        logging.error("Zeroconf library not available. Cannot advertise service.")
        return False
    if service_info:
        logging.warning("Service already being advertised.")
        return True

    try:
        hostname = socket.gethostname()
        service_name = f"Oreon Pickup on {hostname}.{pickup_config.SERVICE_TYPE}"
        # Ensure zeroconf instance is created if not already running from discovery
        if not zeroconf_instance:
            zeroconf_instance = Zeroconf()

        # Get local IP addresses (more robust methods exist, this is basic)
        local_ip = socket.gethostbyname(hostname)
        # For multi-homed systems, finding the *right* IP is complex.
        # Zeroconf often handles finding appropriate addresses itself.

        service_info = ServiceInfo(
            type_=pickup_config.SERVICE_TYPE,
            name=service_name,
            addresses=[socket.inet_aton(local_ip)], # Provide one suggestion
            port=port,
            properties={'version': pickup_config.VERSION, 'hostname': hostname},
            server=f"{hostname}.local."
        )
        logging.info(f"Registering service: {service_name} on port {port}")
        zeroconf_instance.register_service(service_info)
        logging.info("Service advertised successfully.")
        return True
    except Exception as e:
        logging.error(f"Failed to advertise service: {e}")
        if zeroconf_instance and service_info:
            try:
                zeroconf_instance.unregister_service(service_info)
            except Exception as ue:
                logging.error(f"Failed to unregister service during error handling: {ue}")
        service_info = None
        # Don't close the global zeroconf_instance here if discovery might be using it
        return False

def stop_advertising():
    """Stops advertising this device."""
    global zeroconf_instance, service_info
    if not HAS_ZEROCONF or not service_info or not zeroconf_instance:
        logging.info("Service not currently advertised or zeroconf unavailable.")
        return

    logging.info(f"Unregistering service: {service_info.name}")
    try:
        zeroconf_instance.unregister_service(service_info)
        service_info = None
        logging.info("Service stopped advertising.")
        # Only close zeroconf if nothing else is using it (e.g., discovery)
        # This simple example might close it prematurely if discovery is also active.
        # A more robust implementation would manage the Zeroconf instance lifetime better.
        # if not discovery_browser:
        #     zeroconf_instance.close()
        #     zeroconf_instance = None
    except Exception as e:
        logging.error(f"Failed to unregister service: {e}")

# --- Pairing Logic Placeholder --- #

_pairing_server_thread: Optional[threading.Thread] = None
_pairing_socket: Optional[socket.socket] = None
_stop_pairing_server_flag = threading.Event()
_active_pairing_code: Optional[str] = None

def generate_pairing_code() -> str:
    """Generates a simple 4-digit pairing code."""
    return str(random.randint(1000, 9999))

def start_pairing_service(code: str, port: int = pickup_config.DEFAULT_PORT):
    """Starts a temporary server to listen for a pairing connection."""
    global _pairing_server_thread, _stop_pairing_server_flag, _pairing_socket, _active_pairing_code
    if _pairing_server_thread and _pairing_server_thread.is_alive():
        logging.warning("Pairing service already running. Stopping previous instance.")
        stop_pairing_service()

    _active_pairing_code = code
    _stop_pairing_server_flag.clear()
    _pairing_server_thread = threading.Thread(target=_pairing_server_run, args=(code, port), daemon=True)
    _pairing_server_thread.start()
    logging.info(f"Pairing service started on port {port}, waiting for code {code}.")

def _pairing_server_run(expected_code: str, port: int):
    global _pairing_socket
    try:
        _pairing_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _pairing_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        _pairing_socket.bind(("", port)) # Bind to all interfaces
        _pairing_socket.listen(1)
        _pairing_socket.settimeout(1.0) # Timeout for checking stop flag
        logging.info(f"Pairing server listening on port {port}...")

        start_time = time.monotonic()
        connection = None
        addr = None

        while not _stop_pairing_server_flag.is_set():
            if time.monotonic() - start_time > pickup_config.PAIRING_TIMEOUT:
                logging.warning("Pairing timeout reached.")
                break
            try:
                # Use select for non-blocking check with timeout
                ready_to_read, _, _ = select.select([_pairing_socket], [], [], 0.5)
                if ready_to_read:
                    connection, addr = _pairing_socket.accept()
                    logging.info(f"Incoming connection from {addr}")
                    handle_incoming_pairing(connection, addr, expected_code)
                    break # Paired or failed, stop listening
            except socket.timeout:
                continue # No connection yet, check stop flag
            except Exception as e:
                if not _stop_pairing_server_flag.is_set(): # Avoid logging errors during shutdown
                     logging.error(f"Error in pairing server loop: {e}")
                break

    except Exception as e:
        if not _stop_pairing_server_flag.is_set():
            logging.error(f"Failed to start pairing server on port {port}: {e}")
    finally:
        logging.info("Pairing server shutting down.")
        if _pairing_socket:
            _pairing_socket.close()
            _pairing_socket = None
        _active_pairing_code = None # Clear code when server stops

def stop_pairing_service():
    """Signals the pairing server thread to stop."""
    global _pairing_server_thread, _stop_pairing_server_flag, _pairing_socket
    logging.info("Attempting to stop pairing service...")
    _stop_pairing_server_flag.set()
    if _pairing_socket:
        try:
             # Force close the socket to interrupt accept() if it's blocking
             _pairing_socket.close()
        except Exception as e:
            logging.warning(f"Error closing pairing socket during stop: {e}")
    if _pairing_server_thread and _pairing_server_thread.is_alive():
        _pairing_server_thread.join(timeout=2.0)
        if _pairing_server_thread.is_alive():
             logging.warning("Pairing server thread did not stop cleanly.")
    _pairing_server_thread = None
    _pairing_socket = None
    logging.info("Pairing service stop sequence complete.")

def handle_incoming_pairing(connection: socket.socket, addr: tuple, expected_code: str):
    """Handles the logic when another device connects for pairing."""
    try:
        connection.settimeout(10.0) # Timeout for communication
        data = connection.recv(1024).decode('utf-8')
        payload = json.loads(data)

        if payload.get("type") == "pairing_request" and payload.get("code") == expected_code:
            logging.info(f"Correct pairing code received from {addr}")
            response = {"type": "pairing_confirm", "status": "success", "hostname": socket.gethostname()}
            remote_hostname = payload.get("hostname", "Unknown Device") # Get hostname from request
            connection.sendall(json.dumps(response).encode('utf-8'))
            # Store the paired device info
            device_id = f"{remote_hostname}@{addr[0]}" # Example ID format
            device_info = {
                "hostname": remote_hostname,
                "ip": addr[0],
                "port": pickup_config.DEFAULT_PORT, # Assume default port for now
                "paired_at": time.time()
            }
            pickup_state.add_paired_device(device_id, device_info)
            print(f"Successfully paired with {remote_hostname} ({addr[0]})!") # User feedback
        else:
            logging.warning(f"Invalid pairing code or request from {addr}. Expected '{expected_code}', got '{payload.get('code')}'")
            response = {"type": "pairing_confirm", "status": "failure", "reason": "Invalid code"}
            connection.sendall(json.dumps(response).encode('utf-8'))

    except json.JSONDecodeError:
        logging.error(f"Invalid JSON received from {addr}")
    except socket.timeout:
        logging.error(f"Timeout waiting for data from {addr}")
    except Exception as e:
        logging.error(f"Error handling incoming pairing from {addr}: {e}")
    finally:
        connection.close()

def initiate_pairing(target_ip: str, target_port: int, code: str) -> bool:
    """Attempts to connect to another device and pair using the code."""
    logging.info(f"Attempting to pair with {target_ip}:{target_port} using code {code}")
    try:
        with socket.create_connection((target_ip, target_port), timeout=10.0) as sock:
            payload = {"type": "pairing_request", "code": code, "hostname": socket.gethostname()}
            sock.sendall(json.dumps(payload).encode('utf-8'))
            sock.settimeout(10.0)

            data = sock.recv(1024).decode('utf-8')
            response = json.loads(data)

            if response.get("type") == "pairing_confirm" and response.get("status") == "success":
                logging.info(f"Pairing successful with {target_ip}:{target_port}")
                # TODO: Store the paired device info (target_ip, target_port, hostname from response?)
                remote_hostname = response.get("hostname", "Unknown Device")
                # Store the paired device info
                device_id = f"{remote_hostname}@{target_ip}" # Example ID format
                device_info = {
                    "hostname": remote_hostname,
                    "ip": target_ip,
                    "port": target_port,
                    "paired_at": time.time()
                }
                pickup_state.add_paired_device(device_id, device_info)
                print(f"Successfully paired with {remote_hostname} ({target_ip})!") # User feedback
                return True
            else:
                logging.error(f"Pairing failed with {target_ip}. Reason: {response.get('reason', 'Unknown')}")
                return False

    except socket.timeout:
        logging.error(f"Connection timed out trying to reach {target_ip}:{target_port}")
        return False
    except ConnectionRefusedError:
        logging.error(f"Connection refused by {target_ip}:{target_port}")
        return False
    except json.JSONDecodeError:
        logging.error(f"Invalid JSON response received from {target_ip}")
        return False
    except Exception as e:
        logging.error(f"Error initiating pairing with {target_ip}:{target_port}: {e}")
        return False

# --- State Transfer Placeholder --- #

def send_state(target_ip: str, target_port: int, state_data: Dict) -> bool:
    """Sends the current state data to a paired device."""
    logging.warning("send_state not implemented yet.")
    # Needs socket connection, JSON serialization, sending
    # Needs confirmation/error handling
    return False

def start_state_listener(port: int = pickup_config.DEFAULT_PORT):
    """Starts a server to listen for incoming state data."""
    logging.warning("start_state_listener not implemented yet.")
    # Needs server socket, threading, receiving data, JSON deserialization
    # Needs security checks (only accept from paired devices?)
    # Needs mechanism to trigger restore_state
    pass

def stop_state_listener():
     logging.warning("stop_state_listener not implemented yet.")
     pass

# Simple cleanup function for testing
if __name__ == '__main__':
    print("Running basic zeroconf tests...")
    if not HAS_ZEROCONF:
        print("Zeroconf not found, skipping tests.")
        sys.exit(1)

    advertise_service()
    start_discovery()

    print("Advertising and discovering for 10 seconds...")
    try:
        for i in range(10):
            time.sleep(1)
            print(f"Discovered ({i+1}/10): {get_discovered_devices()}")
    except KeyboardInterrupt:
        print("Interrupted.")
    finally:
        print("Cleaning up...")
        stop_discovery()
        stop_advertising()
        # Ensure zeroconf instance is closed if it exists
        if zeroconf_instance:
            zeroconf_instance.close()
        print("Done.") 