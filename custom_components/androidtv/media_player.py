"""Support for functionality to interact with Android TV / Fire TV devices."""
import functools
import logging
import os

from .androidtv.adb_shell.auth.keygen import keygen
from .androidtv.adb_shell.exceptions import (
    InvalidChecksumError,
    InvalidCommandError,
    InvalidResponseError,
    TcpTimeoutException,
)
from .androidtv import ha_state_detection_rules_validator, setup
from .androidtv.constants import APPS, KEYS
import voluptuous as vol

from homeassistant.components.media_player import PLATFORM_SCHEMA, MediaPlayerDevice
from homeassistant.components.media_player.const import (
    SUPPORT_NEXT_TRACK,
    SUPPORT_PAUSE,
    SUPPORT_PLAY,
    SUPPORT_PREVIOUS_TRACK,
    SUPPORT_SELECT_SOURCE,
    SUPPORT_STOP,
    SUPPORT_TURN_OFF,
    SUPPORT_TURN_ON,
    SUPPORT_VOLUME_MUTE,
    SUPPORT_VOLUME_STEP,
)
from homeassistant.const import (
    ATTR_COMMAND,
    ATTR_ENTITY_ID,
    CONF_DEVICE_CLASS,
    CONF_HOST,
    CONF_NAME,
    CONF_PORT,
    STATE_IDLE,
    STATE_OFF,
    STATE_PAUSED,
    STATE_PLAYING,
    STATE_STANDBY,
)
from homeassistant.exceptions import PlatformNotReady
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.storage import STORAGE_DIR

ANDROIDTV_DOMAIN = "androidtv"

_LOGGER = logging.getLogger(__name__)

SUPPORT_ANDROIDTV = (
    SUPPORT_PAUSE
    | SUPPORT_PLAY
    | SUPPORT_TURN_ON
    | SUPPORT_TURN_OFF
    | SUPPORT_PREVIOUS_TRACK
    | SUPPORT_NEXT_TRACK
    | SUPPORT_SELECT_SOURCE
    | SUPPORT_STOP
    | SUPPORT_VOLUME_MUTE
    | SUPPORT_VOLUME_STEP
)

SUPPORT_FIRETV = (
    SUPPORT_PAUSE
    | SUPPORT_PLAY
    | SUPPORT_TURN_ON
    | SUPPORT_TURN_OFF
    | SUPPORT_PREVIOUS_TRACK
    | SUPPORT_NEXT_TRACK
    | SUPPORT_SELECT_SOURCE
    | SUPPORT_STOP
)

CONF_ADBKEY = "adbkey"
CONF_ADB_SERVER_IP = "adb_server_ip"
CONF_ADB_SERVER_PORT = "adb_server_port"
CONF_APPS = "apps"
CONF_GET_SOURCES = "get_sources"
CONF_STATE_DETECTION_RULES = "state_detection_rules"
CONF_TURN_ON_COMMAND = "turn_on_command"
CONF_TURN_OFF_COMMAND = "turn_off_command"

DEFAULT_NAME = "Android TV"
DEFAULT_PORT = 5555
DEFAULT_ADB_SERVER_PORT = 5037
DEFAULT_GET_SOURCES = True
DEFAULT_DEVICE_CLASS = "auto"

DEVICE_ANDROIDTV = "androidtv"
DEVICE_FIRETV = "firetv"
DEVICE_CLASSES = [DEFAULT_DEVICE_CLASS, DEVICE_ANDROIDTV, DEVICE_FIRETV]

SERVICE_ADB_COMMAND = "adb_command"

SERVICE_ADB_COMMAND_SCHEMA = vol.Schema(
    {vol.Required(ATTR_ENTITY_ID): cv.entity_ids, vol.Required(ATTR_COMMAND): cv.string}
)


PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_HOST): cv.string,
        vol.Optional(CONF_DEVICE_CLASS, default=DEFAULT_DEVICE_CLASS): vol.In(
            DEVICE_CLASSES
        ),
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): cv.port,
        vol.Optional(CONF_ADBKEY): cv.isfile,
        vol.Optional(CONF_ADB_SERVER_IP): cv.string,
        vol.Optional(CONF_ADB_SERVER_PORT, default=DEFAULT_ADB_SERVER_PORT): cv.port,
        vol.Optional(CONF_GET_SOURCES, default=DEFAULT_GET_SOURCES): cv.boolean,
        vol.Optional(CONF_APPS, default=dict()): vol.Schema({cv.string: cv.string}),
        vol.Optional(CONF_TURN_ON_COMMAND): cv.string,
        vol.Optional(CONF_TURN_OFF_COMMAND): cv.string,
        vol.Optional(CONF_STATE_DETECTION_RULES, default={}): vol.Schema(
            {cv.string: ha_state_detection_rules_validator(vol.Invalid)}
        ),
    }
)

# Translate from `AndroidTV` / `FireTV` reported state to HA state.
ANDROIDTV_STATES = {
    "off": STATE_OFF,
    "idle": STATE_IDLE,
    "standby": STATE_STANDBY,
    "playing": STATE_PLAYING,
    "paused": STATE_PAUSED,
}


def setup_platform(hass, config, add_entities, discovery_info=None):
    """Set up the Android TV / Fire TV platform."""
    hass.data.setdefault(ANDROIDTV_DOMAIN, {})

    host = f"{config[CONF_HOST]}:{config[CONF_PORT]}"

    if CONF_ADB_SERVER_IP not in config:
        # Use "adb_shell" (Python ADB implementation)
        if CONF_ADBKEY not in config:
            # Generate ADB key files (if they don't exist)
            adbkey = hass.config.path(STORAGE_DIR, "androidtv_adbkey")
            if not os.path.isfile(adbkey):
                keygen(adbkey)

            adb_log = f"using Python ADB implementation with adbkey='{adbkey}'"

            aftv = setup(
                config[CONF_HOST],
                config[CONF_PORT],
                adbkey,
                device_class=config[CONF_DEVICE_CLASS],
                state_detection_rules=config[CONF_STATE_DETECTION_RULES],
                auth_timeout_s=10.0,
            )

        else:
            adb_log = (
                f"using Python ADB implementation with adbkey='{config[CONF_ADBKEY]}'"
            )

            aftv = setup(
                config[CONF_HOST],
                config[CONF_PORT],
                config[CONF_ADBKEY],
                device_class=config[CONF_DEVICE_CLASS],
                state_detection_rules=config[CONF_STATE_DETECTION_RULES],
                auth_timeout_s=10.0,
            )

    else:
        # Use "pure-python-adb" (communicate with ADB server)
        adb_log = f"using ADB server at {config[CONF_ADB_SERVER_IP]}:{config[CONF_ADB_SERVER_PORT]}"

        aftv = setup(
            config[CONF_HOST],
            config[CONF_PORT],
            adb_server_ip=config[CONF_ADB_SERVER_IP],
            adb_server_port=config[CONF_ADB_SERVER_PORT],
            device_class=config[CONF_DEVICE_CLASS],
            state_detection_rules=config[CONF_STATE_DETECTION_RULES],
        )

    if not aftv.available:
        # Determine the name that will be used for the device in the log
        if CONF_NAME in config:
            device_name = config[CONF_NAME]
        elif config[CONF_DEVICE_CLASS] == DEVICE_ANDROIDTV:
            device_name = "Android TV device"
        elif config[CONF_DEVICE_CLASS] == DEVICE_FIRETV:
            device_name = "Fire TV device"
        else:
            device_name = "Android TV / Fire TV device"

        _LOGGER.warning("Could not connect to %s at %s %s", device_name, host, adb_log)
        raise PlatformNotReady

    if host in hass.data[ANDROIDTV_DOMAIN]:
        _LOGGER.warning("Platform already setup on %s, skipping", host)
    else:
        if aftv.DEVICE_CLASS == DEVICE_ANDROIDTV:
            device = AndroidTVDevice(
                aftv,
                config[CONF_NAME],
                config[CONF_APPS],
                config[CONF_GET_SOURCES],
                config.get(CONF_TURN_ON_COMMAND),
                config.get(CONF_TURN_OFF_COMMAND),
            )
            device_name = config[CONF_NAME] if CONF_NAME in config else "Android TV"
        else:
            device = FireTVDevice(
                aftv,
                config[CONF_NAME],
                config[CONF_APPS],
                config[CONF_GET_SOURCES],
                config.get(CONF_TURN_ON_COMMAND),
                config.get(CONF_TURN_OFF_COMMAND),
            )
            device_name = config[CONF_NAME] if CONF_NAME in config else "Fire TV"

        add_entities([device])
        _LOGGER.debug("Setup %s at %s %s", device_name, host, adb_log)
        hass.data[ANDROIDTV_DOMAIN][host] = device

    if hass.services.has_service(ANDROIDTV_DOMAIN, SERVICE_ADB_COMMAND):
        return

    def service_adb_command(service):
        """Dispatch service calls to target entities."""
        cmd = service.data.get(ATTR_COMMAND)
        entity_id = service.data.get(ATTR_ENTITY_ID)
        target_devices = [
            dev
            for dev in hass.data[ANDROIDTV_DOMAIN].values()
            if dev.entity_id in entity_id
        ]

        for target_device in target_devices:
            output = target_device.adb_command(cmd)

            # log the output, if there is any
            if output:
                _LOGGER.info(
                    "Output of command '%s' from '%s': %s",
                    cmd,
                    target_device.entity_id,
                    output,
                )

    hass.services.register(
        ANDROIDTV_DOMAIN,
        SERVICE_ADB_COMMAND,
        service_adb_command,
        schema=SERVICE_ADB_COMMAND_SCHEMA,
    )


def adb_decorator(override_available=False):
    """Wrap ADB methods and catch exceptions.

    Allows for overriding the available status of the ADB connection via the
    `override_available` parameter.
    """

    def _adb_decorator(func):
        """Wrap the provided ADB method and catch exceptions."""

        @functools.wraps(func)
        def _adb_exception_catcher(self, *args, **kwargs):
            """Call an ADB-related method and catch exceptions."""
            if not self.available and not override_available:
                return None

            try:
                return func(self, *args, **kwargs)
            except self.exceptions as err:
                _LOGGER.error(
                    "Failed to execute an ADB command. ADB connection re-"
                    "establishing attempt in the next update. Error: %s",
                    err,
                )
                self.aftv.adb_close()
                self._available = False  # pylint: disable=protected-access
                return None

        return _adb_exception_catcher

    return _adb_decorator


class ADBDevice(MediaPlayerDevice):
    """Representation of an Android TV or Fire TV device."""

    def __init__(
        self, aftv, name, apps, get_sources, turn_on_command, turn_off_command
    ):
        """Initialize the Android TV / Fire TV device."""
        self.aftv = aftv
        self._name = name
        self._app_id_to_name = APPS.copy()
        self._app_id_to_name.update(apps)
        self._app_name_to_id = {
            value: key for key, value in self._app_id_to_name.items()
        }
        self._get_sources = get_sources
        self._keys = KEYS

        self._device_properties = self.aftv.device_properties
        self._unique_id = self._device_properties.get("serialno")

        self.turn_on_command = turn_on_command
        self.turn_off_command = turn_off_command

        # ADB exceptions to catch
        if not self.aftv.adb_server_ip:
            # Using "adb_shell" (Python ADB implementation)
            self.exceptions = (
                AttributeError,
                BrokenPipeError,
                TypeError,
                ValueError,
                InvalidChecksumError,
                InvalidCommandError,
                InvalidResponseError,
                TcpTimeoutException,
            )
        else:
            # Using "pure-python-adb" (communicate with ADB server)
            self.exceptions = (ConnectionResetError, RuntimeError)

        # Property attributes
        self._adb_response = None
        self._available = True
        self._current_app = None
        self._sources = None
        self._state = None

    @property
    def app_id(self):
        """Return the current app."""
        return self._current_app

    @property
    def app_name(self):
        """Return the friendly name of the current app."""
        return self._app_id_to_name.get(self._current_app, self._current_app)

    @property
    def available(self):
        """Return whether or not the ADB connection is valid."""
        return self._available

    @property
    def device_state_attributes(self):
        """Provide the last ADB command's response as an attribute."""
        return {"adb_response": self._adb_response}

    @property
    def name(self):
        """Return the device name."""
        return self._name

    @property
    def should_poll(self):
        """Device should be polled."""
        return True

    @property
    def source(self):
        """Return the current app."""
        return self._app_id_to_name.get(self._current_app, self._current_app)

    @property
    def source_list(self):
        """Return a list of running apps."""
        return self._sources

    @property
    def state(self):
        """Return the state of the player."""
        return self._state

    @property
    def unique_id(self):
        """Return the device unique id."""
        return self._unique_id

    @adb_decorator()
    def media_play(self):
        """Send play command."""
        self.aftv.media_play()

    @adb_decorator()
    def media_pause(self):
        """Send pause command."""
        self.aftv.media_pause()

    @adb_decorator()
    def media_play_pause(self):
        """Send play/pause command."""
        self.aftv.media_play_pause()

    @adb_decorator()
    def turn_on(self):
        """Turn on the device."""
        if self.turn_on_command:
            self.aftv.adb_shell(self.turn_on_command)
        else:
            self.aftv.turn_on()

    @adb_decorator()
    def turn_off(self):
        """Turn off the device."""
        if self.turn_off_command:
            self.aftv.adb_shell(self.turn_off_command)
        else:
            self.aftv.turn_off()

    @adb_decorator()
    def media_previous_track(self):
        """Send previous track command (results in rewind)."""
        self.aftv.media_previous_track()

    @adb_decorator()
    def media_next_track(self):
        """Send next track command (results in fast-forward)."""
        self.aftv.media_next_track()

    @adb_decorator()
    def select_source(self, source):
        """Select input source.

        If the source starts with a '!', then it will close the app instead of
        opening it.
        """
        if isinstance(source, str):
            if not source.startswith("!"):
                self.aftv.launch_app(self._app_name_to_id.get(source, source))
            else:
                source_ = source[1:].lstrip()
                self.aftv.stop_app(self._app_name_to_id.get(source_, source_))

    @adb_decorator()
    def adb_command(self, cmd):
        """Send an ADB command to an Android TV / Fire TV device."""
        key = self._keys.get(cmd)
        if key:
            self.aftv.adb_shell(f"input keyevent {key}")
            self._adb_response = None
            self.schedule_update_ha_state()
            return

        if cmd == "GET_PROPERTIES":
            self._adb_response = str(self.aftv.get_properties_dict())
            self.schedule_update_ha_state()
            return self._adb_response

        response = self.aftv.adb_shell(cmd)
        if isinstance(response, str) and response.strip():
            self._adb_response = response.strip()
        else:
            self._adb_response = None

        self.schedule_update_ha_state()
        return self._adb_response


class AndroidTVDevice(ADBDevice):
    """Representation of an Android TV device."""

    def __init__(
        self, aftv, name, apps, get_sources, turn_on_command, turn_off_command
    ):
        """Initialize the Android TV device."""
        super().__init__(
            aftv, name, apps, get_sources, turn_on_command, turn_off_command
        )

        self._is_volume_muted = None
        self._volume_level = None

    @adb_decorator(override_available=True)
    def update(self):
        """Update the device state and, if necessary, re-connect."""
        # Check if device is disconnected.
        if not self._available:
            # Try to connect
            self._available = self.aftv.adb_connect(always_log_errors=False)

            # To be safe, wait until the next update to run ADB commands if
            # using the Python ADB implementation.
            if not self.aftv.adb_server_ip:
                return

        # If the ADB connection is not intact, don't update.
        if not self._available:
            return

        # Get the updated state and attributes.
        (
            state,
            self._current_app,
            running_apps,
            _,
            self._is_volume_muted,
            self._volume_level,
        ) = self.aftv.update(self._get_sources)

        self._state = ANDROIDTV_STATES.get(state)
        if self._state is None:
            self._available = False

        if running_apps:
            self._sources = [
                self._app_id_to_name.get(app_id, app_id) for app_id in running_apps
            ]
        else:
            self._sources = None

    @property
    def is_volume_muted(self):
        """Boolean if volume is currently muted."""
        return self._is_volume_muted

    @property
    def supported_features(self):
        """Flag media player features that are supported."""
        return SUPPORT_ANDROIDTV

    @property
    def volume_level(self):
        """Return the volume level."""
        return self._volume_level

    @adb_decorator()
    def media_stop(self):
        """Send stop command."""
        self.aftv.media_stop()

    @adb_decorator()
    def mute_volume(self, mute):
        """Mute the volume."""
        self.aftv.mute_volume()

    @adb_decorator()
    def volume_down(self):
        """Send volume down command."""
        self._volume_level = self.aftv.volume_down(self._volume_level)

    @adb_decorator()
    def volume_up(self):
        """Send volume up command."""
        self._volume_level = self.aftv.volume_up(self._volume_level)


class FireTVDevice(ADBDevice):
    """Representation of a Fire TV device."""

    @adb_decorator(override_available=True)
    def update(self):
        """Update the device state and, if necessary, re-connect."""
        # Check if device is disconnected.
        if not self._available:
            # Try to connect
            self._available = self.aftv.adb_connect(always_log_errors=False)

            # To be safe, wait until the next update to run ADB commands if
            # using the Python ADB implementation.
            if not self.aftv.adb_server_ip:
                return

        # If the ADB connection is not intact, don't update.
        if not self._available:
            return

        # Get the `state`, `current_app`, and `running_apps`.
        state, self._current_app, running_apps = self.aftv.update(self._get_sources)

        self._state = ANDROIDTV_STATES.get(state)
        if self._state is None:
            self._available = False

        if running_apps:
            self._sources = [
                self._app_id_to_name.get(app_id, app_id) for app_id in running_apps
            ]
        else:
            self._sources = None

    @property
    def supported_features(self):
        """Flag media player features that are supported."""
        return SUPPORT_FIRETV

    @adb_decorator()
    def media_stop(self):
        """Send stop (back) command."""
        self.aftv.back()
