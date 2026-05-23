#!/usr/bin/env python3
"""Minimal ONVIF Profile S + WS-Discovery server for OpenSecurityCam.
Placeholders __PI_IP__ and __ONVIF_PASSWORD__ are substituted by install.sh.
"""

import base64
import hashlib
import logging
import queue
import re
import socket
import struct
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("onvif")

# ── Config (substituted at install time) ─────────────────────────────────────
DEVICE_IP      = "__PI_IP__"
ONVIF_PORT     = 8090
RTSP_URI       = "rtsp://__PI_IP__:554/h264Preview_01_main"
ONVIF_USER     = "camera"
ONVIF_PASSWORD = "__ONVIF_PASSWORD__"
WIDTH, HEIGHT, FPS, BITRATE, GOP = 960, 720, 10, 2000, 10

MOTION_THRESHOLD = 0.02   # fraction of pixels that must change (0–1)
MOTION_COOLDOWN  = 10.0   # seconds before motion clears

DEVICE_UUID = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"opensecuritycam.{DEVICE_IP}"))

def _read_mac():
    for iface in ("wlan0", "eth0", "end0"):
        try:
            return open(f"/sys/class/net/{iface}/address").read().strip(), iface
        except OSError:
            continue
    return "00:00:00:00:00:00", "wlan0"

DEVICE_MAC, DEVICE_IFS = _read_mac()

# ── Motion state ──────────────────────────────────────────────────────────────
_motion_state       = False
_motion_lock        = threading.Lock()
_motion_clear_timer = None

# pull-point subscriptions: token -> {"expiry": float, "queue": Queue}
_subscriptions = {}
_subs_lock     = threading.Lock()
_request_ctx   = threading.local()   # per-request HTTP path


def _utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _expiry_str(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _enqueue_motion_event(is_motion, operation="Changed"):
    ts = _utcnow()
    now = time.time()
    with _subs_lock:
        for token in list(_subscriptions):
            sub = _subscriptions[token]
            if now > sub["expiry"]:
                del _subscriptions[token]
                continue
            sub["queue"].put((ts, is_motion, operation))


def _set_motion(is_motion):
    global _motion_state
    with _motion_lock:
        if _motion_state == is_motion:
            return
        _motion_state = is_motion
    log.info("Motion: %s", is_motion)
    _enqueue_motion_event(is_motion)


def _on_motion_detected():
    global _motion_clear_timer
    _set_motion(True)
    if _motion_clear_timer is not None:
        _motion_clear_timer.cancel()
    _motion_clear_timer = threading.Timer(MOTION_COOLDOWN, lambda: _set_motion(False))
    _motion_clear_timer.start()


def _motion_detector_loop():
    """Read low-res grayscale frames from RTSP and detect pixel changes."""
    w, h = 160, 90
    frame_size = w * h
    cmd = [
        "ffmpeg", "-loglevel", "quiet",
        "-rtsp_transport", "tcp",
        "-i", RTSP_URI,
        "-vf", f"fps=2,scale={w}:{h}",
        "-f", "rawvideo", "-pix_fmt", "gray", "-",
    ]
    while True:
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            log.info("Motion detector started (pid %d)", proc.pid)
            prev = None
            while True:
                data = proc.stdout.read(frame_size)
                if len(data) < frame_size:
                    break
                if prev is not None:
                    diff = sum(abs(a - b) for a, b in zip(data, prev))
                    score = diff / (frame_size * 255)
                    if score > MOTION_THRESHOLD:
                        log.debug("Motion score %.4f", score)
                        _on_motion_detected()
                prev = data
            proc.wait()
            log.warning("Motion detector exited, restarting in 5s")
        except Exception as e:
            log.error("Motion detector error: %s", e)
        time.sleep(5)


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
        return True  # no header — allow
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
        return base64.b64encode(hashlib.sha1(raw).digest()).decode() == pw_val
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
        f"<tds:GetCapabilitiesResponse><tds:Capabilities>"
        f"<tt:Device><tt:XAddr>http://{DEVICE_IP}:{ONVIF_PORT}/onvif/device_service</tt:XAddr></tt:Device>"
        f"<tt:Events>"
        f"<tt:XAddr>http://{DEVICE_IP}:{ONVIF_PORT}/onvif/event_service</tt:XAddr>"
        f"<tt:WSSubscriptionPolicySupport>false</tt:WSSubscriptionPolicySupport>"
        f"<tt:WSPullPointSupport>true</tt:WSPullPointSupport>"
        f"<tt:WSPausableSubscriptionManagerInterfaceSupport>false</tt:WSPausableSubscriptionManagerInterfaceSupport>"
        f"</tt:Events>"
        f"<tt:Media>"
        f"<tt:XAddr>http://{DEVICE_IP}:{ONVIF_PORT}/onvif/media_service</tt:XAddr>"
        f"<tt:StreamingCapabilities>"
        f"<tt:RTPMulticast>false</tt:RTPMulticast>"
        f"<tt:RTP_TCP>true</tt:RTP_TCP>"
        f"<tt:RTP_RTSP_TCP>true</tt:RTP_RTSP_TCP>"
        f"</tt:StreamingCapabilities>"
        f"</tt:Media>"
        f"</tds:Capabilities></tds:GetCapabilitiesResponse>"
    )

def _GetServices(body):
    return ok(
        f"<tds:GetServicesResponse>"
        f"<tds:Service>"
        f"<tds:Namespace>http://www.onvif.org/ver10/device/wsdl</tds:Namespace>"
        f"<tds:XAddr>http://{DEVICE_IP}:{ONVIF_PORT}/onvif/device_service</tds:XAddr>"
        f"<tds:Version><tt:Major>2</tt:Major><tt:Minor>0</tt:Minor></tds:Version>"
        f"</tds:Service>"
        f"<tds:Service>"
        f"<tds:Namespace>http://www.onvif.org/ver10/events/wsdl</tds:Namespace>"
        f"<tds:XAddr>http://{DEVICE_IP}:{ONVIF_PORT}/onvif/event_service</tds:XAddr>"
        f"<tds:Version><tt:Major>2</tt:Major><tt:Minor>0</tt:Minor></tds:Version>"
        f"</tds:Service>"
        f"<tds:Service>"
        f"<tds:Namespace>http://www.onvif.org/ver10/media/wsdl</tds:Namespace>"
        f"<tds:XAddr>http://{DEVICE_IP}:{ONVIF_PORT}/onvif/media_service</tds:XAddr>"
        f"<tds:Version><tt:Major>2</tt:Major><tt:Minor>0</tt:Minor></tds:Version>"
        f"</tds:Service>"
        f"</tds:GetServicesResponse>"
    )

def _GetScopes(body):
    return ok(
        "<tds:GetScopesResponse>"
        "<tds:Scopes><tt:ScopeDef>Fixed</tt:ScopeDef>"
        "<tt:ScopeItem>onvif://www.onvif.org/name/OpenSecurityCam</tt:ScopeItem></tds:Scopes>"
        "<tds:Scopes><tt:ScopeDef>Fixed</tt:ScopeDef>"
        "<tt:ScopeItem>onvif://www.onvif.org/hardware/RPiZero2W</tt:ScopeItem></tds:Scopes>"
        "<tds:Scopes><tt:ScopeDef>Fixed</tt:ScopeDef>"
        "<tt:ScopeItem>onvif://www.onvif.org/Profile/Streaming</tt:ScopeItem></tds:Scopes>"
        "</tds:GetScopesResponse>"
    )

def _GetHostname(body):
    return ok(
        "<tds:GetHostnameResponse><tds:HostnameInformation>"
        "<tt:FromDHCP>true</tt:FromDHCP>"
        "<tt:Name>camera1</tt:Name>"
        "</tds:HostnameInformation></tds:GetHostnameResponse>"
    )

def _GetNetworkInterfaces(body):
    return ok(
        f'<tds:GetNetworkInterfacesResponse>'
        f'<tds:NetworkInterfaces token="wlan0">'
        f"<tt:Enabled>true</tt:Enabled>"
        f"<tt:Info>"
        f"<tt:Name>{DEVICE_IFS}</tt:Name>"
        f"<tt:HwAddress>{DEVICE_MAC}</tt:HwAddress>"
        f"<tt:MTU>1500</tt:MTU>"
        f"</tt:Info>"
        f"<tt:IPv4>"
        f"<tt:Enabled>true</tt:Enabled>"
        f"<tt:Config>"
        f"<tt:Manual>"
        f"<tt:Address>{DEVICE_IP}</tt:Address>"
        f"<tt:PrefixLength>24</tt:PrefixLength>"
        f"</tt:Manual>"
        f"<tt:DHCP>true</tt:DHCP>"
        f"</tt:Config>"
        f"</tt:IPv4>"
        f"</tds:NetworkInterfaces>"
        f"</tds:GetNetworkInterfacesResponse>"
    )

def _GetNTP(body):
    return ok(
        "<tds:GetNTPResponse><tds:NTPInformation>"
        "<tt:FromDHCP>false</tt:FromDHCP>"
        "</tds:NTPInformation></tds:GetNTPResponse>"
    )

def _GetDNS(body):
    return ok(
        "<tds:GetDNSResponse><tds:DNSInformation>"
        "<tt:FromDHCP>true</tt:FromDHCP>"
        "</tds:DNSInformation></tds:GetDNSResponse>"
    )

def _SystemReboot(body):
    threading.Timer(1.0, lambda: subprocess.run(["sudo", "/sbin/reboot"])).start()
    return ok("<tds:SystemRebootResponse><tds:Message>Rebooting</tds:Message></tds:SystemRebootResponse>")

def _GetServiceCapabilities(body):
    path = getattr(_request_ctx, "path", "")
    if "event" in path:
        return ok(
            '<tev:GetServiceCapabilitiesResponse'
            ' xmlns:tev="http://www.onvif.org/ver10/events/wsdl">'
            '<tev:Capabilities'
            ' WSSubscriptionPolicySupport="false"'
            ' WSPullPointSupport="true"'
            ' WSPausableSubscriptionManagerInterfaceSupport="false"'
            ' MaxNotificationProducers="0"'
            ' MaxPullPoints="10"'
            ' PersistentNotificationStorage="false"/>'
            '</tev:GetServiceCapabilitiesResponse>'
        )
    if "media" in path:
        return ok(
            '<trt:GetServiceCapabilitiesResponse>'
            '<trt:Capabilities SnapshotUri="false" Rotation="false"'
            ' VideoSourceMode="false" OSD="false"/>'
            '</trt:GetServiceCapabilitiesResponse>'
        )
    return ok(
        '<tds:GetServiceCapabilitiesResponse>'
        '<tds:Capabilities>'
        '<tds:Network DNSClient="false" DynDNS="false" IPVersion6="false"'
        ' NTP="0" ZeroConfiguration="false"/>'
        '<tds:Security TLS1.0="false" TLS1.1="false" TLS1.2="false"'
        ' OnboardKeyGeneration="false" AccessPolicyConfig="false"'
        ' DefaultAccessPolicy="false" Dot1X="false" RemoteUserHandling="false"'
        ' X.509Token="false" SAMLToken="false" KerberosToken="false"'
        ' UsernameToken="true" HttpDigest="false" RELToken="false"/>'
        '<tds:System DiscoveryResolve="false" DiscoveryBye="false"'
        ' RemoteDiscovery="false" SystemBackup="false"'
        ' SystemLogging="false" FirmwareUpgrade="false"'
        ' HttpFirmwareUpgrade="false" HttpSystemBackup="false"'
        ' HttpSystemLogging="false" HttpSupportInformation="false"/>'
        '</tds:Capabilities>'
        '</tds:GetServiceCapabilitiesResponse>'
    )


def _GetUsers(body):
    return ok(
        "<tds:GetUsersResponse>"
        f"<tds:User><tt:Username>{ONVIF_USER}</tt:Username>"
        "<tt:UserLevel>Administrator</tt:UserLevel></tds:User>"
        "</tds:GetUsersResponse>"
    )

# ── Event service handlers ────────────────────────────────────────────────────
def _GetEventProperties(body):
    return ok(
        '<tev:GetEventPropertiesResponse'
        ' xmlns:tev="http://www.onvif.org/ver10/events/wsdl"'
        ' xmlns:tns1="http://www.onvif.org/ver10/topics"'
        ' xmlns:wstop="http://docs.oasis-open.org/wsn/t-1"'
        ' xmlns:xs="http://www.w3.org/2001/XMLSchema">'
        '<tev:TopicNamespaceLocation/>'
        '<tev:FixedTopicSet>true</tev:FixedTopicSet>'
        '<tev:TopicSet>'
        '<tns1:RuleEngine>'
        '<tns1:MotionRegionDetector>'
        '<tns1:Motion wstop:topic="true">'
        '<tt:MessageDescription IsProperty="true">'
        '<tt:Source>'
        '<tt:SimpleItemDescription Name="VideoSourceConfigurationToken" Type="tt:ReferenceToken"/>'
        '</tt:Source>'
        '<tt:Key/>'
        '<tt:Data>'
        '<tt:SimpleItemDescription Name="IsMotion" Type="xs:boolean"/>'
        '</tt:Data>'
        '</tt:MessageDescription>'
        '</tns1:Motion>'
        '</tns1:MotionRegionDetector>'
        '</tns1:RuleEngine>'
        '</tev:TopicSet>'
        '</tev:GetEventPropertiesResponse>'
    )


def _CreatePullPointSubscription(body):
    token = str(uuid.uuid4())
    expiry = time.time() + 3600
    q = queue.Queue()
    with _subs_lock:
        _subscriptions[token] = {"expiry": expiry, "queue": q}

    ts = _utcnow()
    with _motion_lock:
        current = _motion_state
    q.put((ts, current, "Initialized"))

    sub_addr = f"http://{DEVICE_IP}:{ONVIF_PORT}/onvif/event_service?token={token}"
    return ok(
        f'<tev:CreatePullPointSubscriptionResponse'
        f' xmlns:tev="http://www.onvif.org/ver10/events/wsdl"'
        f' xmlns:wsnt="http://docs.oasis-open.org/wsn/b-2"'
        f' xmlns:a="http://www.w3.org/2005/08/addressing">'
        f'<tev:SubscriptionReference>'
        f'<a:Address>{sub_addr}</a:Address>'
        f'</tev:SubscriptionReference>'
        f'<wsnt:CurrentTime>{ts}</wsnt:CurrentTime>'
        f'<wsnt:TerminationTime>{_expiry_str(expiry)}</wsnt:TerminationTime>'
        f'</tev:CreatePullPointSubscriptionResponse>'
    )


def _notification_xml(ts, is_motion, operation):
    val = "true" if is_motion else "false"
    return (
        f'<wsnt:NotificationMessage'
        f' xmlns:wsnt="http://docs.oasis-open.org/wsn/b-2"'
        f' xmlns:tns1="http://www.onvif.org/ver10/topics">'
        f'<wsnt:Topic Dialect="http://www.onvif.org/ver10/tev/topicExpression/ConcreteSet">'
        f'tns1:RuleEngine/MotionRegionDetector/Motion'
        f'</wsnt:Topic>'
        f'<wsnt:Message>'
        f'<tt:Message UtcTime="{ts}" PropertyOperation="{operation}">'
        f'<tt:Source>'
        f'<tt:SimpleItem Name="VideoSourceConfigurationToken" Value="vsconf"/>'
        f'</tt:Source>'
        f'<tt:Key/>'
        f'<tt:Data>'
        f'<tt:SimpleItem Name="IsMotion" Value="{val}"/>'
        f'</tt:Data>'
        f'</tt:Message>'
        f'</wsnt:Message>'
        f'</wsnt:NotificationMessage>'
    )


def _subscription_token_from_path():
    path = getattr(_request_ctx, "path", "")
    m = re.search(r"[?&]token=([a-f0-9-]+)", path)
    return m.group(1) if m else None


def _PullMessages(body):
    token = _subscription_token_from_path()
    ts = _utcnow()
    events_xml = ""
    expiry = time.time() + 3600

    if token:
        with _subs_lock:
            sub = _subscriptions.get(token)
            if sub:
                sub["expiry"] = time.time() + 3600
                expiry = sub["expiry"]
                while True:
                    try:
                        event_ts, is_motion, operation = sub["queue"].get_nowait()
                        events_xml += _notification_xml(event_ts, is_motion, operation)
                    except queue.Empty:
                        break

    return ok(
        f'<tev:PullMessagesResponse'
        f' xmlns:tev="http://www.onvif.org/ver10/events/wsdl">'
        f'<tev:CurrentTime>{ts}</tev:CurrentTime>'
        f'<tev:TerminationTime>{_expiry_str(expiry)}</tev:TerminationTime>'
        f'{events_xml}'
        f'</tev:PullMessagesResponse>'
    )


def _Renew(body):
    token = _subscription_token_from_path()
    ts = _utcnow()
    new_expiry = time.time() + 3600
    if token:
        with _subs_lock:
            sub = _subscriptions.get(token)
            if sub:
                sub["expiry"] = new_expiry
    return ok(
        f'<wsnt:RenewResponse xmlns:wsnt="http://docs.oasis-open.org/wsn/b-2">'
        f'<wsnt:CurrentTime>{ts}</wsnt:CurrentTime>'
        f'<wsnt:TerminationTime>{_expiry_str(new_expiry)}</wsnt:TerminationTime>'
        f'</wsnt:RenewResponse>'
    )


def _Unsubscribe(body):
    token = _subscription_token_from_path()
    if token:
        with _subs_lock:
            _subscriptions.pop(token, None)
    return ok('<wsnt:UnsubscribeResponse xmlns:wsnt="http://docs.oasis-open.org/wsn/b-2"/>')


# ── Media service handlers ───────────────────────────────────────────────────
_VS_CONF = (
    f'<tt:VideoSourceConfiguration token="vsconf">'
    f"<tt:Name>VideoSourceConfig</tt:Name>"
    f"<tt:UseCount>1</tt:UseCount>"
    f"<tt:SourceToken>video_src_token</tt:SourceToken>"
    f'<tt:Bounds x="0" y="0" width="{WIDTH}" height="{HEIGHT}"/>'
    f"</tt:VideoSourceConfiguration>"
)

_VE_CONF = (
    f'<tt:VideoEncoderConfiguration token="veconf">'
    f"<tt:Name>VideoEncoderConfig</tt:Name>"
    f"<tt:UseCount>1</tt:UseCount>"
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
    f"<tt:Multicast>"
    f"<tt:Address><tt:Type>IPv4</tt:Type><tt:IPv4Address>0.0.0.0</tt:IPv4Address></tt:Address>"
    f"<tt:Port>0</tt:Port><tt:TTL>0</tt:TTL><tt:AutoStart>false</tt:AutoStart>"
    f"</tt:Multicast>"
    f"<tt:SessionTimeout>PT60S</tt:SessionTimeout>"
    f"</tt:VideoEncoderConfiguration>"
)

_PROFILE = (
    f'<trt:Profiles token="main" fixed="true">'
    f"<tt:Name>MainStream</tt:Name>"
    f"{_VS_CONF}"
    f"{_VE_CONF}"
    f"</trt:Profiles>"
)

def _GetProfiles(body):
    return ok(f"<trt:GetProfilesResponse>{_PROFILE}</trt:GetProfilesResponse>")

def _GetProfile(body):
    return ok(f"<trt:GetProfileResponse>{_PROFILE}</trt:GetProfileResponse>")

def _GetVideoSources(body):
    return ok(
        f'<trt:GetVideoSourcesResponse><trt:VideoSources token="video_src_token">'
        f"<tt:Framerate>{FPS}</tt:Framerate>"
        f"<tt:Resolution><tt:Width>{WIDTH}</tt:Width><tt:Height>{HEIGHT}</tt:Height></tt:Resolution>"
        f"<tt:Imaging/>"
        f"</trt:VideoSources></trt:GetVideoSourcesResponse>"
    )

def _GetVideoSourceConfigurations(body):
    return ok(
        f"<trt:GetVideoSourceConfigurationsResponse>"
        f"{_VS_CONF}"
        f"</trt:GetVideoSourceConfigurationsResponse>"
    )

def _GetVideoEncoderConfigurations(body):
    return ok(
        f"<trt:GetVideoEncoderConfigurationsResponse>"
        f"{_VE_CONF}"
        f"</trt:GetVideoEncoderConfigurationsResponse>"
    )

def _GetVideoEncoderConfigurationOptions(body):
    return ok(
        f"<trt:GetVideoEncoderConfigurationOptionsResponse>"
        f'<trt:Options token="veconf">'
        f"<tt:QualityRange><tt:Min>1</tt:Min><tt:Max>100</tt:Max></tt:QualityRange>"
        f"<tt:H264>"
        f"<tt:ResolutionsAvailable><tt:Width>{WIDTH}</tt:Width><tt:Height>{HEIGHT}</tt:Height></tt:ResolutionsAvailable>"
        f"<tt:GovLengthRange><tt:Min>1</tt:Min><tt:Max>100</tt:Max></tt:GovLengthRange>"
        f"<tt:FrameRateRange><tt:Min>1</tt:Min><tt:Max>30</tt:Max></tt:FrameRateRange>"
        f"<tt:EncodingIntervalRange><tt:Min>1</tt:Min><tt:Max>1</tt:Max></tt:EncodingIntervalRange>"
        f"<tt:H264ProfilesSupported>Baseline</tt:H264ProfilesSupported>"
        f"</tt:H264>"
        f"</trt:Options>"
        f"</trt:GetVideoEncoderConfigurationOptionsResponse>"
    )

def _GetStreamUri(body):
    return ok(
        f"<trt:GetStreamUriResponse><trt:MediaUri>"
        f"<tt:Uri>{RTSP_URI}</tt:Uri>"
        f"<tt:InvalidAfterConnect>false</tt:InvalidAfterConnect>"
        f"<tt:InvalidAfterReboot>false</tt:InvalidAfterReboot>"
        f"<tt:Timeout>PT0S</tt:Timeout>"
        f"</trt:MediaUri></trt:GetStreamUriResponse>"
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

def _GetCompatibleVideoEncoderConfigurations(body):
    return ok(
        f"<trt:GetCompatibleVideoEncoderConfigurationsResponse>"
        f"{_VE_CONF}"
        f"</trt:GetCompatibleVideoEncoderConfigurationsResponse>"
    )

def _GetCompatibleVideoSourceConfigurations(body):
    return ok(
        f"<trt:GetCompatibleVideoSourceConfigurationsResponse>"
        f"{_VS_CONF}"
        f"</trt:GetCompatibleVideoSourceConfigurationsResponse>"
    )

_ACTIONS = {
    # Device service
    "GetSystemDateAndTime":                  (_GetSystemDateAndTime,                 False),
    "GetDeviceInformation":                  (_GetDeviceInformation,                 True),
    "GetCapabilities":                       (_GetCapabilities,                      True),
    "GetServices":                           (_GetServices,                          True),
    "GetScopes":                             (_GetScopes,                            True),
    "GetHostname":                           (_GetHostname,                          True),
    "GetNetworkInterfaces":                  (_GetNetworkInterfaces,                 True),
    "GetNTP":                                (_GetNTP,                               True),
    "GetDNS":                                (_GetDNS,                               True),
    "GetUsers":                              (_GetUsers,                             True),
    "SystemReboot":                          (_SystemReboot,                         True),
    "GetServiceCapabilities":               (_GetServiceCapabilities,               False),
    # Event service
    "GetEventProperties":                    (_GetEventProperties,                   True),
    "CreatePullPointSubscription":           (_CreatePullPointSubscription,          True),
    "CreatePullPointSubscriptionRequest":    (_CreatePullPointSubscription,          True),
    "SubscribeRequest":                      (_CreatePullPointSubscription,          True),
    "Subscribe":                             (_CreatePullPointSubscription,          True),
    "PullMessages":                          (_PullMessages,                         True),
    "PullMessagesRequest":                   (_PullMessages,                         True),
    "Renew":                                 (_Renew,                                True),
    "RenewRequest":                          (_Renew,                                True),
    "Unsubscribe":                           (_Unsubscribe,                          True),
    "UnsubscribeRequest":                    (_Unsubscribe,                          True),
    # Media service
    "GetProfiles":                           (_GetProfiles,                          True),
    "GetProfile":                            (_GetProfile,                           True),
    "GetVideoSources":                       (_GetVideoSources,                      True),
    "GetVideoSourceConfigurations":          (_GetVideoSourceConfigurations,         True),
    "GetVideoEncoderConfigurations":         (_GetVideoEncoderConfigurations,        True),
    "GetVideoEncoderConfigurationOptions":   (_GetVideoEncoderConfigurationOptions,  True),
    "GetStreamUri":                          (_GetStreamUri,                         True),
    "GetSnapshotUri":                        (_GetSnapshotUri,                       True),
    "GetCompatibleVideoEncoderConfigurations": (_GetCompatibleVideoEncoderConfigurations, True),
    "GetCompatibleVideoSourceConfigurations":  (_GetCompatibleVideoSourceConfigurations,  True),
}

# ── HTTP SOAP server ──────────────────────────────────────────────────────────
class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_): pass

    def do_POST(self):
        _request_ctx.path = self.path

        length = int(self.headers.get("Content-Length", 0))
        body   = self.rfile.read(length).decode("utf-8", errors="ignore")

        action = self.headers.get("SOAPAction", "").strip('"').rsplit("/", 1)[-1]
        if not action:
            m = re.search(r"<(?:[^:>]+:)?Body[^>]*>\s*<(?:[^:>]+:)?(\w+)", body)
            if m:
                action = m.group(1)

        entry = _ACTIONS.get(action)
        if entry is None:
            log.warning("Unknown action: %s from %s", action, self.client_address[0])
            self._reply(400, fault(f"Unknown action: {action}"))
            return

        handler, needs_auth = entry
        if needs_auth and not _auth_ok(body):
            log.warning("Auth failed for action: %s", action)
            self._reply(401, fault("Authentication failed"))
            return

        log.info("OK %s from %s", action, self.client_address[0])
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
    "onvif://www.onvif.org/name/OpenSecurityCam "
    "onvif://www.onvif.org/hardware/RPiZero2W "
    "onvif://www.onvif.org/Profile/Streaming"
    "</d:Scopes>"
    "<d:XAddrs>http://{device_ip}:{onvif_port}/onvif/device_service</d:XAddrs>"
    "<d:MetadataVersion>1</d:MetadataVersion>"
    "</d:ProbeMatch></d:ProbeMatches></s:Body></s:Envelope>"
)


def _wsd_loop():
    try:
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
        log.info("WS-Discovery listening on UDP %s:%d", _WSD_GROUP, _WSD_PORT)
    except OSError as e:
        log.error("WS-Discovery socket failed: %s", e)
        return

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
            log.info("WS-Discovery probe answered to %s", addr)
        except Exception as e:
            log.error("WS-Discovery error: %s", e)


if __name__ == "__main__":
    threading.Thread(target=_wsd_loop, daemon=True).start()
    threading.Thread(target=_motion_detector_loop, daemon=True).start()
    log.info("ONVIF HTTP service on port %d, device UUID %s", ONVIF_PORT, DEVICE_UUID)
    HTTPServer(("0.0.0.0", ONVIF_PORT), _Handler).serve_forever()
