"""
mDNS/Bonjour service advertisement for the DVD server.
Advertises _dvds._tcp.local. so client apps can browse for it.
"""
import socket
import logging
from zeroconf import ServiceInfo, Zeroconf

log = logging.getLogger("dvdserver.discovery")

# The service type your client app will browse for.
# Convention: _<name>._tcp.local.  Use a name unique to your project.
SERVICE_TYPE = "_dvds._tcp.local."


def get_lan_ip() -> str:
    """
    Best-effort LAN IP of the interface that has the default route.
    Falls back to 127.0.0.1 if there's no route (offline dev box).
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Doesn't actually send anything; just picks the outbound interface.
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


class DiscoveryService:
    def __init__(self, port: int = 4251, name: str = "DVD MKV Server"):
        self.port = port
        self.name = name
        self.zc: Zeroconf | None = None
        self.info: ServiceInfo | None = None

    def start(self):
        ip = get_lan_ip()
        # Instance name must be unique on the LAN; append hostname so two
        # servers on the same network don't collide.
        instance = f"{self.name} ({socket.gethostname()}).{SERVICE_TYPE}"

        self.info = ServiceInfo(
            type_=SERVICE_TYPE,
            name=instance,
            addresses=[socket.inet_aton(ip)],
            port=self.port,
            properties={
                # Anything you want the client to see *before* it connects.
                # Keep values as short strings — TXT records are small.
                "name": self.name,
                "version": "1",
                "api": "/api/dvds",       # entry-point endpoint
                "http": "1",              # just a hint
                # Optional: advertise a UUID so clients can dedupe restarts
                # "id": "dvd-srv-001",
            },
            server=f"{socket.gethostname()}.local.",
        )

        self.zc = Zeroconf()
        self.zc.register_service(self.info)
        log.info(f"mDNS: advertising '{instance}' at {ip}:{self.port}")

    def stop(self):
        if self.zc and self.info:
            try:
                self.zc.unregister_service(self.info)
            finally:
                self.zc.close()
            log.info("mDNS: advertisement withdrawn")
        self.zc = None
        self.info = None