#!/usr/bin/env python3
"""Minimal ONVIF Profile S + WS-Discovery server for OpenSecurityCam.
Placeholders __PI_IP__ and __ONVIF_PASSWORD__ are substituted by install.sh.
"""

import base64
import hashlib
import re
import socket
import struct
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

# ── Config (substituted at install time) ─────────────────────────────────────
DEVICE_IP      = "__PI_IP__"
ONVIF_PORT     = 8090
RTSP_URI       = "rtsp://__PI_IP__:554/h264Preview_01_main"
ONVIF_USER     = "camera"
ONVIF_PASSWORD = "__ONVIF_PASSWORD__"
WIDTH, HEIGHT, FPS, BITRATE, GOP = 640, 480, 10, 1000, 5

DEVICE_UUID = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"opensecuritycam.{DEVICE_IP}"))

# ── SOAP namespaces ───────────────────────────────────────────────────────────
NS = dict(
    S   = "http://www.w3.org/2003/05/soap-envelope",
    TDS = "http://www.onvif.org/ver10/device/wsdl",
    TRT = "http://www.onvif.org/ver10/media/wsdl",
    TT  = "http://www.onvif.org/ver10/schema",
)

# ── SOAP helpers ──────────────────────────────────────────────────────────────
_ENVELOPE = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<s:Envelope xmlns:s="{S}" xmlns:tds="{TDS}" xmlns:trt="{TRT}" xmlns:tt="{TT}">'
    "<s:Body>{body}</s:Body></s:Envelope>"
).format(**NS, body="{body}")

_FAULT = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<s:Envelope xmlns:s="{S}"><s:Body><s:Fault>'
    "<s:Code><s:Value>s:Sender</s:Value></s:Code>"
    "<s:Reason><s:Text>{{reason}}</s:Text></s:Reason>"
    "</s:Fault></s:Body></s:Envelope>"
).format(**NS)

def ok(body):   return _ENVELOPE.format(body=body)
def fault(why): return _FAULT.format(reason=why)

# ── Auth ──────────────────────────────────────────────────────────────────────
def _auth_ok(xml: str) -> bool:
    user = re.search(r"<[^>]*:?Username[^>]*>([^<]+)<", xml)
    pw   = re.search(r"<[^>]*:?Password[^>]*>([^<]+)<", xml)
    if not user or not pw:
        return True  # no header → allow (discovery phase)
    if user.group(1).strip() != ONVIF_USER:
        return False
    pw_val  = pw.group(1).strip()
    pw_type = re.search(r'Type="([^"]+)"', xml)
    if pw_type and "Digest" in pw_type.group(1):
        nonce   = re.search(r"<[^>]*:?Nonce[^>]*>([^<]+)<", xml)
        created = re.search(r"<[^>]*:?Created[^>]*>([^<]+)<", xml)
        if not nonce or not created:
            return False
        raw = (base64.b64decode(nonce.group(1).strip())
               + created.group(1).strip().encode()
               + ONVIF_PASSWORD.encode())
        expected = base64.b64encode(hashlib.sha1(raw).digest()).decode()
        return pw_val == expected
    return pw_val == ONVIF_PASSWORD

# ── Action handlers ───────────────────────────────────────────────────────────
def _GetSystemDateAndTime(body):
    n = datetime.now(timezone.utc)
    return ok(
        f"<tds:GetSystemDateAndTimeResponse><tds:SystemDateAndTime>"
        f"<tt:DateTimeType>NTP</tt:DateTimeType>"
        f"<tt:DaylightSavings>false</tt:DaylightSavings>"
        f"<tt:TimeZone><tt:TZ>UTC</tt:TZ></tt:TimeZone>"
        f"<tt:UTCDateTime>"
        f"<tt:Time><tt:Hour>{n.hour}</tt:Hour><tt:Minute>{n.minute}</tt:Minute>"
        f"<tt:Second>{n.second}</tt:Second></tt:Time>"
        f"<tt:Date><tt:Year>{n.year}</tt:Year><tt:Month>{n.month}</tt:Month>"
        f"<tt:Day>{n.day}</tt:Day></tt:Date>"
        f"</tt:UTCDateTime></tds:SystemDateAndTime>"
        f"</tds:GetSystemDateAndTimeResponse>"
    )

def _GetDeviceInformation(body):
    return ok(
        "<tds:GetDeviceInformationResponse>"
        "<tds:Manufacturer>OpenSecurityCam</tds:Manufacturer>"
        "<tds:Model>OpenSecurityCam</tds:Model>"
        "<tds:FirmwareVersion>1.0.0</tds:FirmwareVersion>"
        "<tds:SerialNumber>1</tds:SerialNumber>"
        "<tds:HardwareId>RPiZero2W</tds:HardwareId>"
        "</tds:GetDeviceInformationResponse>"
    )

def _GetCapabilities(body):
    return ok(
        f"<tds:GetCapabilitiesResponse><tds:Capabilities><tt:Media>"
        f"<tt:XAddr>http://{DEVICE_IP}:{ONVIF_PORT}/onvif/media_service</tt:XAddr>"
        f"<tt:StreamingCapabilities>"
        f"<tt:RTPMulticast>false</tt:RTPMulticast>"
        f"<tt:RTP_TCP>true</tt:RTP_TCP>"
        f"<tt:RTP_RTSP_TCP>true</tt:RTP_RTSP_TCP>"
        f"</tt:StreamingCapabilities>"
        f"</tt:Media></tds:Capabilities></tds:GetCapabilitiesResponse>"
    )

def _GetServices(body):
    svc = (
        f"<tds:Service>"
        f"<tds:Namespace>http://www.onvif.org/ver10/device/wsdl</tds:Namespace>"
        f"<tds:XAddr>http://{DEVICE_IP}:{ONVIF_PORT}/onvif/device_service</tds:XAddr>"
        f"<tds:Version><tt:Major>2</tt:Major><tt:Minor>0</tt:Minor></tds:Version>"
        f"</tds:Service>"
        f"<tds:Service>"
        f"<tds:Namespace>http://www.onvif.org/ver10/media/wsdl</tds:Namespace>"
        f"<tds:XAddr>http://{DEVICE_IP}:{ONVIF_PORT}/onvif/media_service</tds:XAddr>"
        f"<tds:Version><tt:Major>2</tt:Major><tt:Minor>0</tt:Minor></tds:Version>"
        f"</tds:Service>"
    )
    return ok(f"<tds:GetServicesResponse>{svc}</tds:GetServicesResponse>")

_PROFILE = (
    f'<trt:Profiles token="main" fixed="true">'
    f"<tt:Name>MainStream</tt:Name>"
    f'<tt:VideoSourceConfiguration token="vsconf">'
    f"<tt:Name>VideoSourceConfig</tt:Name><tt:UseCount>1</tt:UseCount>"
    f"<tt:SourceToken>video_src_token</tt:SourceToken>"
    f'<tt:Bounds x="0" y="0" width="{WIDTH}" height="{HEIGHT}"/>'
    f"</tt:VideoSourceConfiguration>"
    f'<tt:VideoEncoderConfiguration token="veconf">'
    f"<tt:Name>VideoEncoderConfig</tt:Name><tt:UseCount>1</tt:UseCount>"
    f"<tt:Encoding>H264</tt:Encoding>"
    f"<tt:Resolution><tt:Width>{WIDTH}</tt:Width><tt:Height>{HEIGHT}</tt:Height></tt:Resolution>"
    f"<tt:Quality>5</tt:Quality>"
    f"<tt:RateControl>"
    f"<tt:FrameRateLimit>{FPS}</tt:FrameRateLimit>"
    f"<tt:EncodingInterval>1</tt:EncodingInterval>"
    f"<tt:BitrateLimit>{BITRATE}</tt:BitrateLimit>"
    f"</tt:RateControl>"
    f"<tt:H264><tt:GovLength>{GOP}</tt:GovLength>"
    f"<tt:H264Profile>Baseline</tt:H264Profile></tt:H264>"
    f"</tt:VideoEncoderConfiguration>"
    f"</trt:Profiles>"
)

def _GetProfiles(body):
    return ok(f"<trt:GetProfilesResponse>{_PROFILE}</trt:GetProfilesResponse>")

def _GetProfile(body):
    return ok(f"<trt:GetProfileResponse>{_PROFILE}</trt:GetProfileResponse>")

def _GetStreamUri(body):
    return ok(
        f"<trt:GetStreamUriResponse><trt:MediaUri>"
        f"<tt:Uri>{RTSP_URI}</tt:Uri>"
        f"<tt:InvalidAfterConnect>false</tt:InvalidAfterConnect>"
        f"<tt:InvalidAfterReboot>false</tt:InvalidAfterReboot>"
        f"<tt:Timeout>PT0S</tt:Timeout>"
        f"</trt:MediaUri></trt:GetStreamUriResponse>"
    )

def _GetVideoSources(body):
    return ok(
        f'<trt:GetVideoSourcesResponse><trt:VideoSources token="video_src_token">'
        f"<tt:Framerate>{FPS}</tt:Framerate>"
        f"<tt:Resolution><tt:Width>{WIDTH}</tt:Width><tt:Height>{HEIGHT}</tt:Height></tt:Resolution>"
        f"</trt:VideoSources></trt:GetVideoSourcesResponse>"
    )

def _GetSnapshotUri(body):
    return ok(
        "<trt:GetSnapshotUriResponse><trt:MediaUri>"
        "<tt:Uri></tt:Uri>"
        "<tt:InvalidAfterConnect>false</tt:InvalidAfterConnect>"
        "<tt:InvalidAfterReboot>false</tt:InvalidAfterReboot>"
        "<tt:Timeout>PT0S</tt:Timeout>"
        "</trt:MediaUri></trt:GetSnapshotUriResponse>"
    )

_ACTIONS = {
    "GetSystemDateAndTime": (_GetSystemDateAndTime, False),
    "GetDeviceInformation": (_GetDeviceInformation, True),
    "GetCapabilities":      (_GetCapabilities,      True),
    "GetServices":          (_GetServices,          True),
    "GetProfiles":          (_GetProfiles,          True),
    "GetProfile":           (_GetProfile,           True),
    "GetStreamUri":         (_GetStreamUri,         True),
    "GetVideoSources":      (_GetVideoSources,      True),
    "GetSnapshotUri":       (_GetSnapshotUri,       True),
}

# ── HTTP SOAP server ──────────────────────────────────────────────────────────
class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_): pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body   = self.rfile.read(length).decode("utf-8", errors="ignore")

        # Determine action from SOAPAction header or body element
        action = self.headers.get("SOAPAction", "").strip('"').rsplit("/", 1)[-1]
        if not action:
            m = re.search(r"<(?:[^:>]+:)?Body[^>]*>\s*<(?:[^:>]+:)?(\w+)", body)
            if m:
                action = m.group(1)

        entry = _ACTIONS.get(action)
        if entry is None:
            self._reply(400, fault(f"Unknown action: {action}"))
            return

        handler, needs_auth = entry
        if needs_auth and not _auth_ok(body):
            self._reply(401, fault("Authentication failed"))
            return

        self._reply(200, handler(body))

    def _reply(self, code, xml):
        data = xml.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/soap+xml; charset=utf-8")
        self.send_header("Content-Length", len(data))
        self.end_headers()
        self.wfile.write(data)


# ── WS-Discovery ──────────────────────────────────────────────────────────────
_WSD_GROUP = "239.255.255.250"
_WSD_PORT  = 3702

_PROBE_MATCH = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<s:Envelope'
    ' xmlns:s="http://www.w3.org/2003/05/soap-envelope"'
    ' xmlns:a="http://schemas.xmlsoap.org/ws/2004/08/addressing"'
    ' xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"'
    ' xmlns:dn="http://www.onvif.org/ver10/network/wsdl">'
    "<s:Header>"
    "<a:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/ProbeMatches</a:Action>"
    "<a:MessageID>urn:uuid:{msg_id}</a:MessageID>"
    "<a:RelatesTo>{relates_to}</a:RelatesTo>"
    "<a:To>http://schemas.xmlsoap.org/ws/2004/08/addressing/role/anonymous</a:To>"
    "</s:Header>"
    "<s:Body><d:ProbeMatches><d:ProbeMatch>"
    "<a:EndpointReference><a:Address>urn:uuid:{device_uuid}</a:Address></a:EndpointReference>"
    "<d:Types>dn:NetworkVideoTransmitter</d:Types>"
    "<d:Scopes>"
    " onvif://www.onvif.org/name/OpenSecurityCam"
    " onvif://www.onvif.org/hardware/RPiZero2W"
    " onvif://www.onvif.org/Profile/Streaming"
    "</d:Scopes>"
    "<d:XAddrs>http://{device_ip}:{onvif_port}/onvif/device_service</d:XAddrs>"
    "<d:MetadataVersion>1</d:MetadataVersion>"
    "</d:ProbeMatch></d:ProbeMatches></s:Body></s:Envelope>"
)


def _wsd_loop():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    except AttributeError:
        pass
    sock.bind(("", _WSD_PORT))
    mreq = struct.pack("4s4s", socket.inet_aton(_WSD_GROUP), socket.inet_aton("0.0.0.0"))
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    sock.settimeout(1.0)
    while True:
        try:
            data, addr = sock.recvfrom(65535)
        except socket.timeout:
            continue
        try:
            text = data.decode("utf-8", errors="ignore")
            if "Probe" not in text or "ProbeMatch" in text:
                continue
            m = re.search(r"<[^>]*:?MessageID[^>]*>(urn:uuid:[^<]+)<", text)
            reply = _PROBE_MATCH.format(
                msg_id=str(uuid.uuid4()),
                relates_to=m.group(1) if m else "",
                device_uuid=DEVICE_UUID,
                device_ip=DEVICE_IP,
                onvif_port=ONVIF_PORT,
            )
            sock.sendto(reply.encode(), addr)
        except Exception:
            pass


if __name__ == "__main__":
    threading.Thread(target=_wsd_loop, daemon=True).start()
    HTTPServer(("0.0.0.0", ONVIF_PORT), _Handler).serve_forever()
