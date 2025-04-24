"""
Network discovery and pairing for Oreon Pickup
"""

import socket
import random
import threading
import logging
from zeroconf import ServiceInfo, Zeroconf, ServiceBrowser
from .pickup_config import DEFAULT_PORT, SERVICE_TYPE, SERVICE_NAME, PAIRING_CODE_LENGTH

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('oreon-pickup-net')

class DeviceDiscoveryListener:
    def __init__(self, update_callback=None):
        self.devices = {}
        self.update_callback = update_callback
        self._zeroconf = None

    def add_service(self, zeroconf, type, name):
        info = zeroconf.get_service_info(type, name)
        if info:
            self.devices[name] = info
            if self.update_callback:
                self.update_callback(self.devices)

    def remove_service(self, zeroconf, type, name):
        if name in self.devices:
            del self.devices[name]
            if self.update_callback:
                self.update_callback(self.devices)

def get_local_ip():
    """Get the local IP address"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def generate_pairing_code():
    """Generate a random pairing code"""
    return ''.join(str(random.randint(0, 9)) for _ in range(PAIRING_CODE_LENGTH))

def start_discovery(update_callback=None):
    """Start discovering other devices"""
    listener = DeviceDiscoveryListener(update_callback)
    zeroconf = Zeroconf()
    browser = ServiceBrowser(zeroconf, SERVICE_TYPE, listener)
    listener._zeroconf = zeroconf
    return listener

def stop_discovery():
    """Stop discovering other devices"""
    # This is a placeholder - actual implementation would need to track
    # and close the Zeroconf instance
    pass

def start_pairing_service(code):
    """Start the pairing service with the given code"""
    # This is a placeholder - actual implementation would start a server
    # listening for pairing requests
    pass

def stop_pairing_service():
    """Stop the pairing service"""
    # This is a placeholder - actual implementation would stop the server
    pass

def initiate_pairing(ip, port, code):
    """Initiate pairing with another device"""
    # This is a placeholder - actual implementation would connect to the
    # other device and attempt to pair
    return True 