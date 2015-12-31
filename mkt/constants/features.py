import base64
import itertools
import math
from collections import OrderedDict

from django.conf import settings

from django.utils.translation import ugettext_lazy as _lazy


# WARNING: When adding a new app feature here also include a migration.
#
# WARNING: Order matters here. Don't re-order these or alphabetize them. If you
# add new ones put them on the end.
#
# These are used to dynamically generate the field list for the AppFeatures
# django model in mkt.webapps.models.
APP_FEATURES = OrderedDict([
    ('APPS', {
        'name': _lazy(u'App Management API'),
        'description': _lazy(u'The app requires the `navigator.mozApps` API '
                             u'to install and manage other apps.'),
        'apis': ('navigator.mozApps',),
    }),
    ('PACKAGED_APPS', {
        'name': _lazy(u'Packaged Apps Install API'),
        'description': _lazy(
            u'The app requires the `navigator.mozApps.installPackage` API '
            u'to install other packaged apps.'),
        'apis': ('navigator.mozApps.installPackage',),
    }),
    ('PAY', {
        'name': _lazy(u'Web Payment'),
        'description': _lazy(u'The app requires the `navigator.mozApps` API.'),
        'apis': ('navigator.pay', 'navigator.mozPay',),
    }),
    ('ACTIVITY', {
        'name': _lazy(u'Web Activities'),
        'description': _lazy(u'The app requires Web Activities '
                             u'(the `MozActivity` API).'),
        'apis': ('MozActivity',),
    }),
    ('LIGHT_EVENTS', {
        'name': _lazy(u'Ambient Light Sensor'),
        'description': _lazy(u'The app requires an ambient light sensor '
                             u'(the `ondevicelight` API).'),
        'apis': ('window.ondevicelight',),
    }),
    ('ARCHIVE', {
        'name': _lazy(u'Archive'),
        'description': _lazy(u'The app requires the `ArchiveReader` API.'),
        'apis': ('ArchiveReader',),
    }),
    ('BATTERY', {
        'name': _lazy(u'Battery'),
        'description': _lazy(u'The app requires the `navigator.battery` API.'),
        'apis': ('navigator.battery',),
    }),
    ('BLUETOOTH', {
        'name': u'Bluetooth',
        'description': _lazy(u'The app requires the `navigator.mozBluetooth` '
                             u'API.'),
        'apis': ('navigator.bluetooth', 'navigator.mozBluetooth'),
    }),
    ('CONTACTS', {
        'name': _lazy(u'Contacts'),
        'description': _lazy(u'The app requires the `navigator.mozContacts` '
                             u'API.'),
        'apis': ('navigator.contacts', 'navigator.mozContacts'),
    }),
    ('DEVICE_STORAGE', {
        'name': _lazy(u'Device Storage'),
        'description': _lazy(u'The app requires the Device Storage API to '
                             u'access files on the filesystem.'),
        'apis': ('navigator.getDeviceStorage',),
    }),
    ('INDEXEDDB', {
        'name': u'IndexedDB',
        'description': _lazy(u'The app requires the platform to support '
                             u'IndexedDB.'),
        'apis': ('navigator.indexedDB', 'navigator.mozIndexedDB'),
    }),
    ('GEOLOCATION', {
        'name': _lazy(u'Geolocation'),
        'description': _lazy(u'The app requires the platform to support the '
                             u'`navigator.geolocation` API.'),
        'apis': ('navigator.geolocation',),
    }),
    ('IDLE', {
        'name': _lazy(u'Idle'),
        'description': _lazy(u'The app requires the platform to support the '
                             u'`addIdleObserver` API.'),
        'apis': ('addIdleObserver', 'removeIdleObserver'),
    }),
    ('NETWORK_INFO', {
        'name': _lazy(u'Network Information'),
        'description': _lazy(u'The app requires the ability to get '
                             u'information about the network connection (the '
                             u'`navigator.mozConnection` API).'),
        'apis': ('navigator.mozConnection', 'navigator.mozMobileConnection'),
    }),
    ('NETWORK_STATS', {
        'name': _lazy(u'Network Stats'),
        'description': _lazy(u'The app requires the '
                             u'`navigator.mozNetworkStats` API.'),
        'apis': ('navigator.networkStats', 'navigator.mozNetworkStats'),
    }),
    ('PROXIMITY', {
        'name': _lazy(u'Proximity'),
        'description': _lazy(u'The app requires a proximity sensor (the '
                             u'`ondeviceproximity` API).'),
        'apis': ('navigator.ondeviceproximity',),
    }),
    ('PUSH', {
        'name': _lazy(u'Simple Push'),
        'description': _lazy(u'The app requires the `navigator.mozPush` API.'),
        'apis': ('navigator.push', 'navigator.mozPush'),
    }),
    ('ORIENTATION', {
        'name': _lazy(u'Screen Orientation'),
        'description': _lazy(u'The app requires the platform to support the '
                             u'`ondeviceorientation` API.'),
        'apis': ('ondeviceorientation',),
    }),
    ('TIME_CLOCK', {
        'name': _lazy(u'Time/Clock'),
        'description': _lazy(u'The app requires the `navigator.mozTime` API.'),
        'apis': ('navigator.time', 'navigator.mozTime'),
    }),
    ('VIBRATE', {
        'name': _lazy(u'Vibration'),
        'description': _lazy(u'The app requires the device to support '
                             u'vibration (the `navigator.vibrate` API).'),
        'apis': ('navigator.vibrate',),
    }),
    ('FM', {
        'name': u'WebFM',
        'description': _lazy(u'The app requires the `navigator.mozFM` or '
                             u'`navigator.mozFMRadio` APIs.'),
        'apis': ('navigator.mozFM', 'navigator.mozFMRadio'),
    }),
    ('SMS', {
        'name': u'WebSMS',
        'description': _lazy(u'The app requires the `navigator.mozSms` API.'),
        'apis': ('navigator.mozSms', 'navigator.mozSMS'),
    }),
    ('TOUCH', {
        'name': _lazy(u'Touch'),
        'description': _lazy(u'The app requires the platform to support touch '
                             u'events. This option indicates that the app '
                             u'will not function when used with a mouse.'),
        'apis': ('window.ontouchstart',),
    }),
    ('QHD', {
        'name': _lazy(u'Smartphone-Sized Displays (qHD)'),
        'description': _lazy(u'The app requires the platform to have a '
                             u'smartphone-sized display (having qHD '
                             u'resolution). This option indicates that the '
                             u'app will be unusable on larger displays '
                             u'(e.g., tablets, desktop, large or high-DPI '
                             u'phones).'),
        'apis': (),
    }),
    ('MP3', {
        'name': u'MP3',
        'description': _lazy(u'The app requires that the platform can decode '
                             u'and play MP3 files.'),
        'apis': (),
    }),
    ('AUDIO', {
        'name': _lazy(u'Audio'),
        'description': _lazy(u'The app requires that the platform supports '
                             u'the HTML5 audio API.'),
        'apis': ('Audio',),
    }),
    ('WEBAUDIO', {
        'name': _lazy(u'Web Audio'),
        'description': _lazy(u'The app requires that the platform supports '
                             u'the Web Audio API (`window.AudioContext`).'),
        'apis': ('AudioContext', 'mozAudioContext', 'webkitAudioContext'),
    }),
    ('VIDEO_H264', {
        'name': u'H.264',
        'description': _lazy(u'The app requires that the platform can decode '
                             u'and play H.264 video files.'),
        'apis': (),
    }),
    ('VIDEO_WEBM', {
        'name': u'WebM',
        'description': _lazy(u'The app requires that the platform can decode '
                             u'and play WebM video files (VP8).'),
        'apis': (),
    }),
    ('FULLSCREEN', {
        'name': _lazy(u'Full Screen'),
        'description': _lazy(u'The app requires the Full Screen API '
                             u'(`requestFullScreen` or '
                             u'`mozRequestFullScreen`).'),
        'apis': ('document.documentElement.requestFullScreen',),
    }),
    ('GAMEPAD', {
        'name': _lazy(u'Gamepad'),
        'description': _lazy(u'The app requires the platform to support the '
                             u'gamepad API (`navigator.getGamepads`).'),
        'apis': ('navigator.getGamepad', 'navigator.mozGetGamepad'),
    }),
    ('QUOTA', {
        'name': _lazy(u'Quota Management'),
        'description': _lazy(u'The app requires the platform to allow '
                             u'persistent storage limit increases above the '
                             u'normally allowed limits for an app '
                             u'(`window.StorageInfo` or '
                             u'`window.persistentStorage`).'),
        'apis': ('navigator.persistentStorage', 'navigator.temporaryStorage'),
    }),
    ('CAMERA', {
        'name': _lazy(u'Camera'),
        'description': _lazy(u'The app requires the platform to allow access '
                             u'to video from the device camera via a '
                             u'LocalMediaStream object.'),
        'apis': ('navigator.getUserMedia({video: true, picture: true})',),
    }),
    ('MIC', {
        'name': _lazy(u'Microphone'),
        'description': _lazy(u'The app requires the platform to allow access '
                             u'to audio from the device microphone.'),
        'apis': ('navigator.getUserMedia({audio: true})',),
    }),
    ('SCREEN_CAPTURE', {
        'name': _lazy(u'Screen Capture'),
        'description': _lazy(u'The app requires the platform to allow access '
                             u'to the device screen for capture.'),
        'apis': ('navigator.getUserMedia({video: {mandatory: '
                 '{chromeMediaSource: "screen"}}})',),
    }),
    ('WEBRTC_MEDIA', {
        'name': _lazy(u'WebRTC MediaStream'),
        'description': _lazy(u'The app requires the platform to allow web '
                             u'real-time communication browser-to-browser '
                             u'inbound media streams.'),
        'apis': ('MediaStream',),
    }),
    ('WEBRTC_DATA', {
        'name': _lazy(u'WebRTC DataChannel'),
        'description': _lazy(u'The app requires the platform to allow '
                             u'peer-to-peer exchange of data other than audio '
                             u'and video.'),
        'apis': ('DataChannel',),
    }),
    ('WEBRTC_PEER', {
        'name': _lazy(u'WebRTC PeerConnection'),
        'description': _lazy(u'The app requires the platform to allow '
                             u'communication of streaming data between '
                             u'peers.'),
        'apis': ('RTCPeerConnection',),
    }),
    ('SPEECH_SYN', {
        'name': _lazy(u'Web Speech Synthesis'),
        'description': _lazy(u'The app requires the platform to allow the use '
                             u'of text-to-speech.'),
        'apis': ('SpeechSynthesis',)
    }),
    ('SPEECH_REC', {
        'name': _lazy(u'Web Speech Recognition'),
        'description': _lazy(u'The app requires the platform to allow '
                             u'the use of speech-to-text.'),
        'apis': ('SpeechRecognition',)
    }),
    ('POINTER_LOCK', {
        'name': _lazy(u'Pointer Lock'),
        'description': _lazy(u'The app requires the platform to provide '
                             u'additional information and control about the '
                             u'pointer.'),
        'apis': ('document.documentElement.requestPointerLock',)
    }),
    ('NOTIFICATION', {
        'name': _lazy(u'Notifications'),
        'description': _lazy(u'The app requires the platform to allow the '
                             u'displaying phone and desktop notifications to '
                             u'the user.'),
        'apis': ('Notification', 'navigator.mozNotification')
    }),
    ('ALARM', {
        'name': _lazy(u'Alarms'),
        'description': _lazy(u'The app requires the platform to provide '
                             u'access to the device alarm settings to '
                             u'schedule notifications and events at specific '
                             u'time.'),
        'apis': ('navigator.mozAlarms',)
    }),
    ('SYSTEMXHR', {
        'name': _lazy(u'SystemXHR'),
        'description': _lazy(u'The app requires the platform to allow the '
                             u'sending of asynchronous HTTP requests without '
                             u'the restrictions of the same-origin policy.'),
        'apis': ('XMLHttpRequest({mozSystem: true})',)
    }),
    ('TCPSOCKET', {
        'name': _lazy(u'TCP Sockets'),
        'description': _lazy(u'The app requires the platform to allow opening '
                             u'raw TCP sockets.'),
        'apis': ('TCPSocket', 'navigator.mozTCPSocket')
    }),
    ('THIRDPARTY_KEYBOARD_SUPPORT', {
        'name': _lazy(u'Third-Party Keyboard Support'),
        'description': _lazy(u'The app requires the platform to support '
                             u'third-party keyboards.'),
        'apis': ('navigator.mozInputMethod',),
    }),
    ('NETWORK_INFO_MULTIPLE', {
        'name': _lazy(u'Multiple Network Information'),
        'description': _lazy(u'The app requires the ability to get '
                             u'information about multiple network '
                             u'connections.'),
        'apis': ('navigator.mozMobileConnections',),
    }),
    ('MOBILEID', {
        'name': _lazy(u'Mobile ID'),
        'description': _lazy(u'The app requires access to the '
                             u'`navigator.getMobileIdAssertion` API.'),
        'apis': ('navigator.getMobileIdAssertion',),
    }),
    ('PRECOMPILE_ASMJS', {
        'name': _lazy(u'Asm.js Precompilation'),
        'description': _lazy(u'The app requires the device to support '
                             u'precompilation of asm.js code.'),
        'apis': (),
    }),
    ('HARDWARE_512MB_RAM', {
        'name': _lazy(u'512MB RAM Device'),
        'description': _lazy(u'The app requires the device to have at least '
                             u'512MB RAM.'),
        'apis': (),
    }),
    ('HARDWARE_1GB_RAM', {
        'name': _lazy(u'1GB RAM Device'),
        'description': _lazy(u'The app requires the device to have at least '
                             u'1GB RAM.'),
        'apis': (),
    }),
    ('NFC', {
        'name': _lazy(u'NFC'),
        'description': _lazy(u'The app requires access to the Near Field '
                             u'Communication (NFC) API.'),
        'apis': ('navigator.mozNfc',),
    }),
    ('OPENMOBILEACL', {
        # This feature requirement is limited to partners for now, and
        # therefore has no description, no translation, and will not be shown
        # to regular developers.
        'name': u'OpenMobile ACL',
        'description': '',
        'apis': (),
        'hidden': True,
    }),
    ('UDPSOCKET', {
        'name': _lazy(u'UDP Sockets'),
        'description': _lazy(u'The app requires the platform to allow opening '
                             u'raw UDP sockets.'),
        'apis': ('UDPSocket',)
    }),
])


class FeaturesBitField(object):
    """
    BitField class that stores the bits into several integers, and can
    import/export from/to base64. Designed that way to be compatible with the
    way we export the features signature in JavaScript.
    """

    def __init__(self, size, values=None):
        """
        Instantiate a FeaturesBitField of size `size`. Optional parameter
        `values` allows you to override the initial list of integers used to
        store the values.
        """
        self.size = size
        if values is not None:
            self.values = values
        else:
            self.values = [0] * int(math.ceil(self.size / 8.0))

    def get(self, i):
        index = int(math.floor(i / 8.0))
        bit = i % 8
        return (self.values[index] & (1 << bit)) != 0

    def set(self, i, value):
        index = int(math.floor(i / 8.0))
        bit = i % 8
        if value:
            self.values[index] |= 1 << bit
        else:
            self.values[index] &= ~(1 << bit)

    def to_base64(self):
        return base64.b64encode(''.join([chr(i) for i in self.values]))

    def to_list(self):
        return [self.get(i) for i in range(0, self.size)]

    @classmethod
    def from_list(cls, data):
        instance = cls(len(data))
        for i, v in enumerate(data):
            instance.set(i, v)
        return instance

    @classmethod
    def from_base64(cls, string, size):
        return cls(size, values=[ord(c) for c in base64.b64decode(string)])


class FeatureProfile(OrderedDict):
    """
    Convenience class for performing conversion operations on feature profile
    representations.
    """

    def __init__(self, _default=False, **kwargs):
        """
        Creates a FeatureProfile object.

        Takes kwargs to the features to enable or disable. Features not
        specified but that are in APP_FEATURES will be False by default.

        E.g.:

            >>> FeatureProfile(sms=True).to_signature()
            '400.32.1'

        """
        super(FeatureProfile, self).__init__()
        for af in APP_FEATURES:
            key = af.lower()
            self[key] = kwargs.get(key, _default)

    @classmethod
    def from_int(cls, features, limit=None):
        """
        Construct a FeatureProfile object from a integer bitfield.

        >>> FeatureProfile.from_int(0x42)
        FeatureProfile([('apps', False), ('packaged_apps', True), ...)
        """
        instance = cls()  # Defaults to everything set to False.
        if limit is None:
            limit = len(APP_FEATURES)
        app_features_to_consider = OrderedDict(
            itertools.islice(APP_FEATURES.iteritems(), limit))
        for i, k in enumerate(reversed(app_features_to_consider)):
            instance[k.lower()] = bool(features & 1 << i)
        return instance

    @classmethod
    def from_list(cls, features, limit=None):
        """
        Construct a FeatureProfile object from a list of boolean values.

        >>> FeatureProfile.from_list([True, False, ...])
        FeatureProfile([('apps', True), ('packaged_apps', False), ...)
        """
        instance = cls()  # Defaults to everything set to False.
        if limit is None:
            limit = len(APP_FEATURES)
        app_features_to_consider = OrderedDict(
            itertools.islice(APP_FEATURES.iteritems(), limit))
        for i, k in enumerate(app_features_to_consider):
            instance[k.lower()] = bool(features[i])
        return instance

    @classmethod
    def from_decimal_signature(cls, signature):
        """
        Construct a FeatureProfile object from a decimal signature.

        >>> FeatureProfile.from_signature('40000000.32.1')
        FeatureProfile([('apps', False), ('packaged_apps', True), ...)
        """
        # If the signature is invalid, let the ValueError be raised, it's up to
        # the caller to decide what to do with it.
        number, limit, version = signature.split('.')
        return cls.from_int(int(number, 16), limit=int(limit))

    @classmethod
    def from_base64_signature(cls, signature):
        """
        Construct a FeatureProfile object from a base64 signature.

        >>> FeatureProfile.from_signature('=////////Hw==.53.9')
        FeatureProfile([('apps', True), ('packaged_apps', True), ...)
        """
        # If the signature is invalid, let the ValueError be raised, it's up to
        # the caller to decide what to do with it.
        string, limit, version = signature.split('.')
        limit = int(limit)

        # Decode base64 string (ignoring the leading '=' that is used to
        # indicate we are dealing with a base64 signature) using our bit field.
        bitfield = FeaturesBitField.from_base64(string[1:], limit)
        # Build the FeatureProfile from our list of boolean values.
        return cls.from_list(bitfield.to_list(), limit=limit)

    @classmethod
    def from_signature(cls, signature):
        """
        Construct a FeatureProfile object from a signature, base64
        (starting with '=') or decimal (everything else).

        >>> FeatureProfile.from_signature('40000000.32.1')
        FeatureProfile([('apps', False), ('packaged_apps', True), ...)

        >>> FeatureProfile.from_signature('=////////Hw==.53.9')
        FeatureProfile([('apps', True), ('packaged_apps', True), ...)
        """
        if signature.startswith('='):
            return cls.from_base64_signature(signature)
        return cls.from_decimal_signature(signature)

    def to_int(self):
        """
        Convert a FeatureProfile object to an integer bitfield.

        >>> profile.to_int()
        66
        """
        features = 0
        for i, v in enumerate(reversed(self.values())):
            features |= bool(v) << i
        return features

    def to_signature(self):
        """
        Convert a FeatureProfile object to its decimal signature.

        >>> profile.to_signature()
        '40000000.32.1'
        """
        return '%x.%s.%s' % (self.to_int(), len(self),
                             settings.APP_FEATURES_VERSION)

    def to_base64_signature(self):
        """
        Convert a FeatureProfile object to its base64 signature.

        >>> profile.to_signature()
        '=////////Hw==.53.9'
        """
        self.bitfield = FeaturesBitField.from_list(self.values())
        return '=%s.%s.%s' % (self.bitfield.to_base64(), len(self),
                              settings.APP_FEATURES_VERSION)

    def to_list(self):
        """
        Returns a list representing the true values of this profile.
        """
        return [k for k, v in self.iteritems() if v]

    def to_kwargs(self, prefix=''):
        """
        Returns a dict representing the false values of this profile.

        Parameters:
        - `prefix` - a string prepended to the key name. Helpful if being used
                     to traverse relations

        This only includes keys for which the profile is False, which is useful
        for querying apps where we want to filter by apps which do not require
        a feature.

        >>> profile = FeatureProject.from_signature(request.get('pro'))
        >>> Webapp.objects.filter(**profile.to_kwargs())

        """
        return dict((prefix + k, False) for k, v in self.iteritems() if not v)

    def has_features(self, required_features):
        """Returns whether this profile has all the features listed in the
        `required_features` parameter.

        `required_features` is expected to be a simple list of feature keys,
        like so: ['packaged_apps', 'alarm'].
        """
        return set(required_features).issubset(self.to_list())
