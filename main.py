import PySimpleGUI as sg
import serial
import serial.tools.list_ports
import threading
import time
import json
from enum import Enum, auto
from typing import Dict, List, Optional, Tuple, Union, Callable
from dataclasses import dataclass, field
import os
import ctypes
import platform

os.environ["XDG_SESSION_TYPE"] = "xcb"


# Global UI scaling parameter
UI_SCALE = 2.4  # Adjust this value to change UI scaling (e.g., 1.0, 1.25, 1.5, 2.0)

# Apply scaling settings
sg.set_options(
    scaling=UI_SCALE,
    font=("Segoe UI" if platform.system() == "Windows" else "Helvetica", 10),
)

# Constants
WINDOW_TITLE = "ESPKenisis Radio Link Manager"
THEME = "DarkBlue14"  # A variation of DarkBlue with better contrast
REFRESH_RATE_MS = 100
DEBUG = True  # Set to True to enable debug prints


def debug_print(*args, **kwargs):
    """Print debug messages if DEBUG is enabled"""
    if DEBUG:
        print("[DEBUG]", *args, **kwargs)


class ChannelState(Enum):
    """Enum for channel states"""

    DEFAULT = auto()  # No override
    STATE_1 = auto()  # First state
    STATE_2 = auto()  # Second state
    STATE_3 = auto()  # Third state (only for flight mode)


@dataclass
class Channel:
    """Class representing a channel with its states and current value"""

    name: str
    channel_number: int
    states: List[str]
    has_third_state: bool = False
    state: ChannelState = ChannelState.DEFAULT
    value: int = 0  # Actual value sent to the target

    def get_state_text(self) -> str:
        """Get the text representation of the current state"""
        if self.state == ChannelState.DEFAULT:
            return "DEFAULT"
        elif self.state == ChannelState.STATE_1:
            return self.states[0]
        elif self.state == ChannelState.STATE_2:
            return self.states[1]
        elif self.state == ChannelState.STATE_3 and self.has_third_state:
            return self.states[2]
        return "UNKNOWN"


@dataclass
class Target:
    """Class representing a target (RX) with its channels and status"""

    target_id: int
    override_enabled: bool = False
    last_update_time: float = 0
    signal_strength: int = 0
    battery_voltage: float = 0.0
    status: str = "Unknown"
    custom_data: Dict = field(default_factory=dict)
    channels: Dict[int, Channel] = field(default_factory=dict)

    def __post_init__(self):
        """Initialize the channels after class instantiation"""
        if not self.channels:
            self.channels = {
                5: Channel("Arm", 5, ["ARMED", "DISARMED"]),
                6: Channel("Kill", 6, ["KILLED", "ACTIVE"]),
                7: Channel("Flight Mode", 7, ["MANUAL", "POSITION", "MISSION"], True),
                8: Channel("Offboard", 8, ["ENABLED", "DISABLED"]),
            }

    def get_override_payload(self) -> Dict:
        """Generate a data payload to send to the transmitter"""
        payload = {
            "target_id": self.target_id,
            "override": self.override_enabled,
            "channels": {},
        }

        if self.override_enabled:
            for ch_num, channel in self.channels.items():
                if channel.state != ChannelState.DEFAULT:
                    payload["channels"][str(ch_num)] = channel.state.value

        return payload

    def update_from_data(self, data: Dict) -> None:
        """Update target from received data packet"""
        self.last_update_time = time.time()

        # Update known fields
        if "signal" in data:
            self.signal_strength = data["signal"]
        if "battery" in data:
            self.battery_voltage = data["battery"]
        if "status" in data:
            self.status = data["status"]
        if "override" in data:
            self.override_enabled = bool(data["override"])

        # Update channel values if included
        if "channels" in data and isinstance(data["channels"], dict):
            for ch_num_str, value in data["channels"].items():
                try:
                    ch_num = int(ch_num_str)
                    if ch_num in self.channels:
                        self.channels[ch_num].value = value
                except (ValueError, KeyError):
                    pass

        # Store all other fields in custom_data for display/future use
        for key, value in data.items():
            if key not in ["signal", "battery", "status", "id", "override", "channels"]:
                self.custom_data[key] = value


class ESPKenisisManager:
    """Manages the serial connection to the ESPKenisis transmitter"""

    def __init__(
        self,
        callback_on_target_update: Callable[[Dict], None],
        callback_on_error: Callable[[str], None],
    ):
        self.port = None
        self.serial_conn = None
        self.is_connected = False
        self.is_running = False
        self.read_thread = None
        self.targets = {}  # Dictionary of targets indexed by target_id
        self.callback_on_target_update = callback_on_target_update
        self.callback_on_error = callback_on_error
        self.raw_data_handlers = {}  # Dictionary of message type handlers

        # Register default handlers
        self._register_default_handlers()

    def _register_default_handlers(self):
        """Register default handlers for different message types"""
        self.register_handler("target_state", self._handle_target_state)
        self.register_handler("tx_channels", self._handle_tx_channels)
        self.register_handler("error", self._handle_error)

    def register_handler(self, msg_type: str, handler_func: Callable[[Dict], None]):
        """Register a handler for a specific message type"""
        self.raw_data_handlers[msg_type] = handler_func

    def get_available_ports(self) -> List[str]:
        """Get list of available serial ports"""
        return [port.device for port in serial.tools.list_ports.comports()]

    def connect(self, port: str, baudrate: int = 115200) -> bool:
        """Connect to the specified serial port"""
        try:
            self.serial_conn = serial.Serial(port, baudrate, timeout=1)
            self.port = port
            self.is_connected = True
            self.is_running = True

            # Start the read thread
            self.read_thread = threading.Thread(target=self._read_serial, daemon=True)
            self.read_thread.start()

            # Request transmitter info
            self._send_command({"command": "get_info"})
            return True
        except Exception as e:
            self.callback_on_error(f"Connection error: {e}")
            return False

    def disconnect(self):
        """Disconnect from the serial port"""
        self.is_running = False
        if self.read_thread:
            self.read_thread.join(timeout=1.0)

        if self.serial_conn and self.serial_conn.is_open:
            self.serial_conn.close()

        self.is_connected = False
        self.port = None
        self.serial_conn = None
        self.targets = {}

    def send_target_override(self, target: Target):
        """Send target override data to the transmitter"""
        if not self.is_connected:
            return

        payload = target.get_override_payload()
        self._send_command({"command": "set_override", "data": payload})

    def _send_command(self, command_dict: Dict):
        """Send a command as JSON to the serial port"""
        if not self.is_connected or not self.serial_conn:
            return

        try:
            command_json = json.dumps(command_dict) + "\n"
            self.serial_conn.write(command_json.encode())
        except Exception as e:
            self.callback_on_error(f"Error sending command: {e}")

    def _read_serial(self):
        """Read and process data from the serial port"""
        if not self.serial_conn:
            return

        buffer = ""

        while self.is_running:
            try:
                if self.serial_conn.in_waiting > 0:
                    new_data = self.serial_conn.read(
                        self.serial_conn.in_waiting
                    ).decode()
                    buffer += new_data

                    # Process complete lines
                    lines = buffer.split("\n")
                    buffer = lines.pop()  # Keep the last incomplete line

                    for line in lines:
                        line = line.strip()
                        if not line:
                            continue

                        try:
                            data = json.loads(line)
                            self._process_message(data)
                        except json.JSONDecodeError:
                            self.callback_on_error(f"Invalid JSON: {line}")

                time.sleep(0.01)  # Small sleep to prevent CPU hogging
            except Exception as e:
                self.callback_on_error(f"Error reading serial: {e}")
                self.is_running = False

    def _process_message(self, data: Dict):
        """Process messages received from the transmitter"""
        if "type" not in data:
            return

        msg_type = data.get("type")

        # Call registered handler for this message type
        if msg_type in self.raw_data_handlers:
            self.raw_data_handlers[msg_type](data)
        else:
            # Default behavior for unknown message types
            self.callback_on_target_update(data)

    def _handle_target_state(self, data: Dict):
        """Handle target state messages"""
        if "targets" not in data:
            debug_print("Received target_state message without 'targets' field")
            return

        targets_data = data["targets"]
        debug_print(f"Processing {len(targets_data)} targets from update")

        for target_data in targets_data:
            if "id" not in target_data:
                debug_print("Target data missing 'id' field:", target_data)
                continue

            target_id = target_data["id"]
            debug_print(f"Processing target ID {target_id}")

            # Create target if doesn't exist
            if target_id not in self.targets:
                debug_print(f"Creating new target with ID {target_id}")
                self.targets[target_id] = Target(target_id=target_id)

            # Update target with received data
            target = self.targets[target_id]
            target.update_from_data(target_data)
            debug_print(
                f"Updated target {target_id}: status={target.status}, signal={target.signal_strength}, battery={target.battery_voltage}"
            )

            # Notify UI
            debug_print(f"Notifying UI of update for target {target_id}")
            self.callback_on_target_update(
                {"type": "target_update", "target_id": target_id}
            )

    def _handle_tx_channels(self, data: Dict):
        """Handle transmitter channel data"""
        if "channels" not in data:
            return

        # Process channel data
        channel_data = data["channels"]

        # Notify UI of channel update
        self.callback_on_target_update({"type": "tx_channels", "data": channel_data})

    def _handle_error(self, data: Dict):
        """Handle error messages"""
        if "message" in data:
            self.callback_on_error(data["message"])

    def get_target_ids(self) -> List[int]:
        """Get list of all known target IDs"""
        return sorted(list(self.targets.keys()))

    def get_target(self, target_id: int) -> Optional[Target]:
        """Get a target by ID"""
        return self.targets.get(target_id)


class ESPKenisisUI:
    """Main UI class for the ESPKenisis Radio Link Manager"""

    def __init__(self):
        self.window = None
        self.manager = ESPKenisisManager(
            callback_on_target_update=self._on_target_update,
            callback_on_error=self._on_error,
        )
        self.target_frames = {}  # Dictionary of target frames by ID
        self.tx_channels_display = None  # For displaying TX channel data
        self.frame_update_pending = False  # Flag to track if we need to rebuild frames
        self.last_frame_update = 0  # Time tracking for throttling updates
        self._init_ui()

    def _init_ui(self):
        """Initialize the UI layout"""
        sg.theme(THEME)

        # Connection section
        connection_layout = [
            [
                sg.Text("Serial Port:"),
                sg.Combo(
                    self.manager.get_available_ports(), key="-PORT-", size=(20, 1)
                ),
                sg.Button("Refresh", key="-REFRESH-PORTS-"),
                sg.Button("Connect", key="-CONNECT-"),
                sg.Button("Disconnect", key="-DISCONNECT-", disabled=True),
            ]
        ]

        # TX Channels section
        tx_channels_layout = [
            [sg.Text("No TX channel data available", key="-TX-CHANNELS-")]
        ]

        # Targets section (will contain target frames)
        self.targets_layout = [
            [sg.Text("Connect to ESPKenisis transmitter to view targets")]
        ]

        # Important: When we need to completely rebuild the targets section,
        # we'll use this approach instead of trying to update individual elements
        self.targets_container = sg.Column(
            self.targets_layout,
            key="-TARGETS-COLUMN-",
            scrollable=True,
            size=(800, 400),
        )

        # Status bar
        status_layout = [
            [
                sg.Text(
                    "Not connected",
                    key="-STATUS-",
                    size=(60, 1),
                    relief=sg.RELIEF_SUNKEN,
                )
            ]
        ]

        # Main layout
        layout = [
            [sg.Frame("Connection", connection_layout, font=("Helvetica", 12))],
            [
                sg.Frame(
                    "TX Channels",
                    tx_channels_layout,
                    key="-TX-FRAME-",
                    font=("Helvetica", 12),
                )
            ],
            [
                self.targets_container
            ],  # Reference the container rather than creating a new one
            [
                sg.Frame("Status", status_layout, font=("Helvetica", 12)),
                sg.Button("Clear Log", key="-CLEAR-LOG-"),
            ],
        ]

        # Create window
        self.window = sg.Window(
            WINDOW_TITLE,
            layout,
            finalize=True,
            resizable=True,
            size=(850, 700),
            icon=sg.DEFAULT_BASE64_ICON,
        )

        # Initialize TX channels display
        self.tx_channels_display = self.window["-TX-CHANNELS-"]

    def _get_element_key(self, target_id, element_type, sub_id=None):
        """Generate consistent element keys for all target elements

        Args:
            target_id: The target ID
            element_type: The type of element (STATUS, SIGNAL, BATT, etc.)
            sub_id: Optional sub-identifier (like channel number)

        Returns:
            A consistently formatted key string
        """
        # PySimpleGUI uses "-KEY-" format by convention
        if sub_id is not None:
            return f"-TARGET-{target_id}-{element_type}-{sub_id}-"
        else:
            return f"-TARGET-{target_id}-{element_type}-"

    def _create_target_frame(self, target_id: int) -> sg.Frame:
        """Create a frame containing all controls for a target"""
        target = self.manager.get_target(target_id)
        if not target:
            return sg.Frame(
                f"Target {target_id}",
                [[sg.Text("No data available", font=("Helvetica", 10))]],
            )

        debug_print(f"Creating frame for target {target_id}")

        # Generate keys for all elements
        frame_key = self._get_element_key(target_id, "FRAME")
        override_key = self._get_element_key(target_id, "OVERRIDE")
        status_key = self._get_element_key(target_id, "STATUS")
        signal_key = self._get_element_key(target_id, "SIGNAL")
        batt_key = self._get_element_key(target_id, "BATT")

        debug_print(
            f"Keys for target {target_id}: frame={frame_key}, status={status_key}"
        )

        # Override button and basic info
        override_row = [
            sg.Text(f"Target {target_id} Control", font=("Helvetica", 10, "bold")),
            sg.Button(
                "Override: OFF",
                key=override_key,
                enable_events=True,
                font=("Helvetica", 10),
            ),
            sg.Text("Status:", font=("Helvetica", 10)),
            sg.Text(
                target.status, key=status_key, size=(10, 1), font=("Helvetica", 10)
            ),
            sg.Text("Signal:", font=("Helvetica", 10)),
            sg.Text(
                f"{target.signal_strength}",
                key=signal_key,
                size=(6, 1),
                font=("Helvetica", 10),
            ),
            sg.Text("Battery:", font=("Helvetica", 10)),
            sg.Text(
                f"{target.battery_voltage:.2f}V",
                key=batt_key,
                size=(6, 1),
                font=("Helvetica", 10),
            ),
        ]

        # Create channel controls
        channel_rows = []
        for ch_num, channel in target.channels.items():
            options = ["DEFAULT"] + channel.states
            ch_key = self._get_element_key(target_id, "CH", ch_num)
            channel_rows.append(
                [
                    sg.Text(f"CH{ch_num}: {channel.name}", font=("Helvetica", 10)),
                    sg.Combo(
                        options,
                        default_value="DEFAULT",
                        key=ch_key,
                        enable_events=True,
                        disabled=True,
                        size=(10, 1),
                        font=("Helvetica", 10),
                    ),
                ]
            )

        # Custom data display
        custom_data_rows = []
        if target.custom_data:
            custom_data_rows.append(
                [sg.Text("Additional Data:", font=("Helvetica", 10, "bold"))]
            )
            for key, value in target.custom_data.items():
                data_key = self._get_element_key(target_id, "DATA", key)
                custom_data_rows.append(
                    [
                        sg.Text(f"{key}:", size=(12, 1), font=("Helvetica", 10)),
                        sg.Text(
                            f"{value}",
                            key=data_key,
                            size=(15, 1),
                            font=("Helvetica", 10),
                        ),
                    ]
                )

        # Combine rows
        frame_layout = [override_row] + channel_rows + custom_data_rows

        return sg.Frame(
            f"Target {target_id}", frame_layout, key=frame_key, font=("Helvetica", 11)
        )

    def _create_new_window_layout(self):
        """Create a full window layout with current state
        This is used when we need to completely rebuild the window"""
        debug_print("Creating new window layout")

        # First get current state so we can preserve it
        current_port = None

        if self.window and "-PORT-" in self.window.key_dict:
            try:
                current_port = self.window["-PORT-"].get()
                debug_print(f"Current port value: {current_port}")
            except:
                debug_print("Could not get current port value")

        # Get TX channels text
        tx_text = "No TX channel data available"
        if self.tx_channels_display:
            try:
                tx_text = self.tx_channels_display.get()
                debug_print(f"Current TX text: {tx_text}")
            except:
                debug_print("Could not get current TX text")

        # Connection section
        connection_layout = [
            [
                sg.Text("Serial Port:", font=("Helvetica", 10)),
                sg.Combo(
                    self.manager.get_available_ports(),
                    key="-PORT-",
                    size=(20, 1),
                    default_value=current_port,
                    font=("Helvetica", 10),
                ),
                sg.Button("Refresh", key="-REFRESH-PORTS-", font=("Helvetica", 10)),
                sg.Button(
                    "Connect",
                    key="-CONNECT-",
                    disabled=self.manager.is_connected,
                    font=("Helvetica", 10),
                ),
                sg.Button(
                    "Disconnect",
                    key="-DISCONNECT-",
                    disabled=not self.manager.is_connected,
                    font=("Helvetica", 10),
                ),
            ]
        ]

        # TX Channels section
        tx_channels_layout = [
            [sg.Text(tx_text, key="-TX-CHANNELS-", font=("Helvetica", 10))]
        ]

        # Create target frames
        self.target_frames = {}  # Clear old references
        target_ids = self.manager.get_target_ids()
        debug_print(f"Creating frames for {len(target_ids)} targets: {target_ids}")

        if not target_ids:
            # No targets
            targets_layout = [
                [sg.Text("No targets detected yet", font=("Helvetica", 10))]
            ]
        else:
            # Create a layout for each target
            targets_layout = []
            for target_id in target_ids:
                try:
                    debug_print(f"Creating frame for target {target_id} in new layout")
                    frame = self._create_target_frame(target_id)
                    targets_layout.append([frame])
                    self.target_frames[target_id] = frame
                    debug_print(
                        f"Successfully created frame for target {target_id} in new layout"
                    )
                except Exception as e:
                    debug_print(
                        f"Error creating frame for target {target_id} in new layout: {e}"
                    )

        # Status bar text
        status_text = "Not connected"
        if self.manager.is_connected:
            status_text = (
                f"Connected to {self.manager.port}. {len(target_ids)} targets detected."
            )

        # Status bar
        status_layout = [
            [
                sg.Text(
                    status_text,
                    key="-STATUS-",
                    size=(60, 1),
                    relief=sg.RELIEF_SUNKEN,
                    font=("Helvetica", 10),
                )
            ]
        ]

        # Main layout - use variable size values for column for better scrolling
        debug_print(f"Creating main layout with {len(targets_layout)} target frames")
        layout = [
            [sg.Frame("Connection", connection_layout, font=("Helvetica", 12))],
            [
                sg.Frame(
                    "TX Channels",
                    tx_channels_layout,
                    key="-TX-FRAME-",
                    font=("Helvetica", 12),
                )
            ],
            [
                sg.Column(
                    targets_layout,
                    key="-TARGETS-COLUMN-",
                    scrollable=True,
                    size=(800, 400),
                    vertical_scroll_only=True,
                )
            ],
            [
                sg.Frame("Status", status_layout, font=("Helvetica", 12)),
                sg.Button("Clear Log", key="-CLEAR-LOG-"),
            ],
        ]

        debug_print(f"Layout creation completed with {len(self.target_frames)} frames")
        return layout

    def _update_target_frames(self):
        """Update the UI with frames for each target"""
        debug_print("========== REBUILDING ALL TARGET FRAMES ==========")

        # This method is always failing to update UI - let's immediately recreate the window
        debug_print("Skipping normal update and going straight to window recreation")
        succeeded = self._recreate_window()

        if succeeded:
            debug_print("Window recreation successful")
        else:
            debug_print("Window recreation failed - will try again later")
            self.frame_update_pending = True

        debug_print("========== TARGET FRAME REBUILD COMPLETE ==========")
        return succeeded

    def _update_target_display(self, target_id: int):
        """Update display for a specific target"""
        target = self.manager.get_target(target_id)
        if not target:
            debug_print(
                f"Cannot update display for target {target_id} - target not found"
            )
            return

        debug_print(f"Target update requested for target {target_id}")

        # SIMPLIFICATION: Instead of trying to update individual elements,
        # we'll always rebuild all frames. This is less efficient but more reliable.
        current_time = time.time()
        if (current_time - self.last_frame_update) > 1.0:  # Throttle updates
            debug_print(f"Rebuilding all frames for target update {target_id}")
            try:
                self._update_target_frames()
                self.last_frame_update = current_time
            except Exception as e:
                print(f"Error rebuilding frames: {e}")
                debug_print(f"Exception rebuilding frames: {e}")
        else:
            # Schedule an update for later if we're updating too frequently
            debug_print(f"Scheduling frame rebuild for next cycle (throttled)")
            self.frame_update_pending = True

    def _update_tx_channels_display(self, channel_data: Dict):
        """Update the TX channels display"""
        if not channel_data:
            return

        # Format channel data for display
        formatted_text = "TX Channels: "
        for ch_num, value in channel_data.items():
            formatted_text += f"CH{ch_num}: {value} | "

        # Update display
        self.tx_channels_display.update(formatted_text)

    def _on_target_update(self, data: Dict):
        """Callback when target data is updated"""
        try:
            # We're using thread-safe communication via event values
            debug_print(f"Target update callback with data: {data}")

            # For sanity checking - only trigger UI updates for major changes
            # This helps prevent the window from constantly reopening

            # Only send UI updates for specific event types
            include_event = False

            if "type" in data:
                # Always include tx_channels updates (doesn't cause window recreation)
                if data["type"] == "tx_channels":
                    include_event = True

                # For target updates, only include if we know it's relevant
                elif data["type"] == "target_update" and "target_id" in data:
                    target_id = data["target_id"]

                    # Always include new targets we haven't seen before
                    if target_id not in self.target_frames:
                        include_event = True
                    # For existing targets, we'll update less frequently (in the event handler)

            if include_event:
                # Convert the data to a serializable format first
                serialized_data = json.dumps(data)

                # Use this to signal the main thread to update the UI
                debug_print(f"Posting event to main thread: {serialized_data[:100]}...")
                self.window.write_event_value("-TARGET-UPDATE-", serialized_data)
            else:
                debug_print(
                    f"Skipping UI update for this event type: {data.get('type', 'unknown')}"
                )

        except Exception as e:
            print(f"Error in _on_target_update: {e}")
            debug_print(f"Target update callback error: {e}")

            # Try a simpler approach if serialization fails
            if isinstance(data, dict) and "target_id" in data:
                debug_print(
                    f"Falling back to simple target update with ID: {data['target_id']}"
                )

                # Again, only forward new targets
                if data["target_id"] not in self.target_frames:
                    self.window.write_event_value(
                        "-SIMPLE-TARGET-UPDATE-", data["target_id"]
                    )
            else:
                debug_print(f"Could not extract target_id from data: {data}")

    def _on_error(self, error_msg: str):
        """Callback when an error occurs"""
        try:
            # Schedule GUI update in the main thread
            self.window.write_event_value("-ERROR-", error_msg)
        except Exception as e:
            print(f"Error in _on_error: {e}")

    def _handle_channel_change(self, target_id: int, ch_num: int, value: str):
        """Handle changes to channel values"""
        debug_print(
            f"Handling channel change for target {target_id}, channel {ch_num}, value {value}"
        )

        target = self.manager.get_target(target_id)
        if not target:
            debug_print(f"Target {target_id} not found")
            return

        channel = target.channels.get(ch_num)
        if not channel:
            debug_print(f"Channel {ch_num} not found in target {target_id}")
            return

        # Update channel state based on selected value
        debug_print(f"Current channel state: {channel.state}, setting to: {value}")
        if value == "DEFAULT":
            channel.state = ChannelState.DEFAULT
        elif value == channel.states[0]:
            channel.state = ChannelState.STATE_1
        elif value == channel.states[1]:
            channel.state = ChannelState.STATE_2
        elif channel.has_third_state and value == channel.states[2]:
            channel.state = ChannelState.STATE_3

        debug_print(f"Updated channel state to: {channel.state}")

        # Send updated data to transmitter
        self.manager.send_target_override(target)
        debug_print(f"Sent override data for target {target_id} after channel change")

    def _recreate_window(self):
        """Recreate the entire window - use this when layout updates are failing"""
        debug_print("RECREATING ENTIRE WINDOW")

        try:
            # Get list of target IDs before doing anything
            target_ids = self.manager.get_target_ids()
            debug_print(f"Targets to display in new window: {target_ids}")

            # Remember state before closing old window
            old_location = None
            old_size = (850, 700)
            is_connected = self.manager.is_connected
            port = self.manager.port

            if self.window:
                try:
                    old_location = self.window.CurrentLocation()
                    old_size = self.window.size
                    debug_print(
                        f"Captured window location: {old_location} and size: {old_size}"
                    )
                except Exception as e:
                    debug_print(f"Could not get window location/size: {e}")

                # Close old window - important to avoid resource leaks
                try:
                    self.window.close()
                    debug_print("Old window closed")
                except Exception as e:
                    debug_print(f"Error closing old window: {e}")

            # Create new layout and window
            layout = self._create_new_window_layout()
            debug_print("New layout created")

            # Create a new window with the same properties as the old one
            self.window = sg.Window(
                WINDOW_TITLE,
                layout,
                finalize=True,
                resizable=True,
                size=old_size,
                location=old_location,
                # Explicitly set keep_on_top for better UI experience during recreation
                keep_on_top=True,
                # Set titlebar icon for better aesthetics (only applied on Windows)
                icon=sg.DEFAULT_BASE64_ICON,
            )
            debug_print("New window created and finalized")

            # Reinitialize displays
            self.tx_channels_display = self.window["-TX-CHANNELS-"]

            # Update connection button states to match current state
            if is_connected:
                self.window["-CONNECT-"].update(disabled=True)
                self.window["-DISCONNECT-"].update(disabled=False)
                self.window["-STATUS-"].update(
                    f"Connected to {port}. {len(target_ids)} targets detected."
                )
            else:
                self.window["-CONNECT-"].update(disabled=False)
                self.window["-DISCONNECT-"].update(disabled=True)
                self.window["-STATUS-"].update("Not connected")

            debug_print(
                f"Window recreated with {len(self.target_frames)} target frames"
            )
            self.last_frame_update = time.time()

            return True

        except Exception as e:
            print(f"Critical error recreating window: {e}")
            debug_print(f"CRITICAL ERROR recreating window: {e}")
            import traceback

            debug_print(f"Traceback: {traceback.format_exc()}")
            return False

    def run(self):
        """Main event loop"""
        while True:
            event, values = self.window.read(timeout=REFRESH_RATE_MS)

            # Check if we need to rebuild frames (throttled to prevent excessive updates)
            current_time = time.time()
            # Use a much longer throttle time to prevent constant flickering
            if (
                self.frame_update_pending
                and (current_time - self.last_frame_update) > 3.0
            ):  # Throttle to once per 3 seconds
                debug_print("Performing deferred frame update")
                try:
                    # First try normal update
                    self._update_target_frames()
                    self.frame_update_pending = False
                    self.last_frame_update = current_time
                except Exception as e:
                    debug_print(
                        f"Error during deferred frame update, will try window recreation: {e}"
                    )

                    # If that fails, try recreating the whole window
                    try:
                        self._recreate_window()
                        self.frame_update_pending = False
                        self.last_frame_update = current_time
                    except Exception as e:
                        debug_print(f"Critical error during window recreation: {e}")
                        # Will retry again later

            if event == sg.WINDOW_CLOSED:
                break

            elif event == "-REFRESH-PORTS-":
                ports = self.manager.get_available_ports()
                self.window["-PORT-"].update(values=ports)

            elif event == "-CONNECT-":
                port = values["-PORT-"]
                if port:
                    if self.manager.connect(port):
                        self.window["-CONNECT-"].update(disabled=True)
                        self.window["-DISCONNECT-"].update(disabled=False)
                        self.window["-STATUS-"].update(
                            f"Connected to {port}. Waiting for target data..."
                        )

                        # Schedule a window rebuild shortly after connection
                        # This ensures we show new targets when they arrive
                        self.frame_update_pending = True
                        self.last_frame_update = 0  # Force immediate refresh
                    else:
                        self.window["-STATUS-"].update(f"Failed to connect to {port}")

            elif event == "-DISCONNECT-":
                self.manager.disconnect()
                self.window["-CONNECT-"].update(disabled=False)
                self.window["-DISCONNECT-"].update(disabled=True)
                self.window["-STATUS-"].update("Disconnected")
                # Clear targets display
                self.window["-TARGETS-COLUMN-"].layout(
                    [[sg.Text("Connect to ESPKenisis transmitter to view targets")]]
                )
                # Clear TX channels display
                self.tx_channels_display.update("No TX channel data available")

            elif event == "-TARGET-UPDATE-":
                debug_print(f"Received TARGET-UPDATE event with data: {values[event]}")
                try:
                    # Deserialize the data that was passed via write_event_value
                    serialized_data = values[event]

                    # Handle both string and dict types for backward compatibility
                    if isinstance(serialized_data, str):
                        try:
                            data = json.loads(serialized_data)
                        except json.JSONDecodeError:
                            debug_print(f"Failed to parse JSON: {serialized_data}")
                            return
                    else:
                        data = serialized_data

                    debug_print(f"Parsed data: {data}")

                    # Only show TX channel updates right away
                    if "type" in data:
                        if data["type"] == "target_update" and "target_id" in data:
                            # Only update if we have a lot of changes or this is the first time
                            target_id = data["target_id"]
                            target = self.manager.get_target(target_id)

                            if target:
                                # Check if this target is already displayed
                                if target_id not in self.target_frames:
                                    debug_print(
                                        f"Target {target_id} not yet displayed, scheduling update"
                                    )
                                    self.frame_update_pending = True
                                else:
                                    debug_print(
                                        f"Target {target_id} already displayed, no update needed"
                                    )
                            else:
                                debug_print(f"Target {target_id} not found in manager")

                        elif data["type"] == "tx_channels" and "data" in data:
                            debug_print(f"Updating TX channels: {data['data']}")
                            # This is a simpler update that doesn't involve target frames
                            self._update_tx_channels_display(data["data"])

                except Exception as e:
                    print(f"Error processing target update: {e}")
                    debug_print(f"Exception during target update: {e}")

            elif event == "-SIMPLE-TARGET-UPDATE-":
                # Fallback for when JSON serialization fails
                target_id = values[event]
                debug_print(f"Received SIMPLE-TARGET-UPDATE for target_id: {target_id}")

                # Check if this target is already displayed
                if target_id not in self.target_frames:
                    debug_print(
                        f"Target {target_id} not yet displayed, scheduling update"
                    )
                    self.frame_update_pending = True
                else:
                    debug_print(
                        f"Target {target_id} already displayed, no update needed"
                    )

            elif event == "-ERROR-":
                error_msg = values[event]
                self.window["-STATUS-"].update(f"ERROR: {error_msg}")

            elif event == "-CLEAR-LOG-":
                self.window["-STATUS-"].update("Status cleared")

            # Handle override toggle events
            elif event.startswith("-TARGET-") and "-OVERRIDE-" in event:
                try:
                    # Extract target_id from the event key: -TARGET-1-OVERRIDE-
                    parts = event.split("-")
                    # Format is: ['', 'TARGET', '1', 'OVERRIDE', '']
                    target_id = int(parts[2])

                    debug_print(f"Override toggle event for target {target_id}")
                    target = self.manager.get_target(target_id)
                    if target:
                        # Toggle the override state
                        target.override_enabled = not target.override_enabled

                        # Update button text
                        btn_text = (
                            "Override: ON"
                            if target.override_enabled
                            else "Override: OFF"
                        )
                        self.window[event].update(text=btn_text)

                        # Enable/disable channel controls
                        for ch_num in target.channels:
                            ch_key = self._get_element_key(target_id, "CH", ch_num)
                            ch_element = self.window.find_element(
                                ch_key, silent_on_error=True
                            )
                            if ch_element:
                                ch_element.update(disabled=not target.override_enabled)
                                debug_print(
                                    f"Updated channel {ch_num} enabled state to {target.override_enabled}"
                                )

                        # Send updated data
                        self.manager.send_target_override(target)
                        debug_print(f"Sent override data for target {target_id}")
                except Exception as e:
                    print(f"Error handling override toggle: {e}")
                    debug_print(f"Exception during override toggle: {e}")

            # Handle channel change events
            elif event.startswith("-TARGET-") and "-CH-" in event:
                try:
                    # Parse the event key: -TARGET-1-CH-5-
                    parts = event.split("-")
                    # Format is: ['', 'TARGET', '1', 'CH', '5', '']
                    if len(parts) >= 5:
                        target_id = int(parts[2])
                        ch_num = int(parts[4])
                        debug_print(
                            f"Channel change event for target {target_id}, ch {ch_num}, value: {values[event]}"
                        )
                        self._handle_channel_change(target_id, ch_num, values[event])
                except Exception as e:
                    print(f"Error handling channel change: {e}")
                    debug_print(f"Exception during channel change: {e}")

        # Clean up
        self.manager.disconnect()
        self.window.close()


if __name__ == "__main__":
    app = ESPKenisisUI()
    app.run()
