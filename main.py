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

os.environ["XDG_SESSION_TYPE"] = "xcb"


# Constants
WINDOW_TITLE = "ESPKenisis Radio Link Manager"
THEME = "DarkBlue"
REFRESH_RATE_MS = 100


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

        # Store all other fields in custom_data for display/future use
        for key, value in data.items():
            if key not in ["signal", "battery", "status"]:
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
            return

        targets_data = data["targets"]
        for target_data in targets_data:
            if "id" not in target_data:
                continue

            target_id = target_data["id"]

            # Create target if doesn't exist
            if target_id not in self.targets:
                self.targets[target_id] = Target(target_id=target_id)

            # Update target with received data
            target = self.targets[target_id]
            target.update_from_data(target_data)

            # Notify UI
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
            [sg.Frame("Connection", connection_layout, font="Any 1")],
            [
                sg.Frame(
                    "TX Channels", tx_channels_layout, key="-TX-FRAME-", font="Any 12"
                )
            ],
            [
                sg.Column(
                    self.targets_layout,
                    key="-TARGETS-COLUMN-",
                    scrollable=True,
                    size=(800, 400),
                )
            ],
            [
                sg.Frame("Status", status_layout, font="Any 12"),
                sg.Button("Clear Log", key="-CLEAR-LOG-"),
            ],
        ]

        # Create window
        self.window = sg.Window(
            WINDOW_TITLE, layout, finalize=True, resizable=True, size=(850, 700)
        )

        # Initialize TX channels display
        self.tx_channels_display = self.window["-TX-CHANNELS-"]

    def _create_target_frame(self, target_id: int) -> sg.Frame:
        """Create a frame containing all controls for a target"""
        target = self.manager.get_target(target_id)
        if not target:
            return sg.Frame(f"Target {target_id}", [[sg.Text("No data available")]])

        # Override button
        override_row = [
            sg.Text(f"Target {target_id} Control"),
            sg.Button(
                "Override: OFF",
                key=f"-TARGET-{target_id}-OVERRIDE-",
                enable_events=True,
            ),
            sg.Text("Status:"),
            sg.Text(target.status, key=f"-TARGET-{target_id}-STATUS-", size=(10, 1)),
            sg.Text("Signal:"),
            sg.Text(
                f"{target.signal_strength}",
                key=f"-TARGET-{target_id}-SIGNAL-",
                size=(6, 1),
            ),
            sg.Text("Battery:"),
            sg.Text(
                f"{target.battery_voltage:.2f}V",
                key=f"-TARGET-{target_id}-BATT-",
                size=(6, 1),
            ),
        ]

        # Create channel controls
        channel_rows = []
        for ch_num, channel in target.channels.items():
            options = ["DEFAULT"] + channel.states
            channel_rows.append(
                [
                    sg.Text(f"CH{ch_num}: {channel.name}"),
                    sg.Combo(
                        options,
                        default_value="DEFAULT",
                        key=f"-TARGET-{target_id}-CH-{ch_num}-",
                        enable_events=True,
                        disabled=True,
                        size=(10, 1),
                    ),
                ]
            )

        # Custom data display
        custom_data_rows = []
        if target.custom_data:
            custom_data_rows.append([sg.Text("Additional Data:", font="Any 10 bold")])
            for key, value in target.custom_data.items():
                custom_data_rows.append(
                    [
                        sg.Text(f"{key}:", size=(12, 1)),
                        sg.Text(
                            f"{value}",
                            key=f"-TARGET-{target_id}-DATA-{key}-",
                            size=(15, 1),
                        ),
                    ]
                )

        # Combine rows
        frame_layout = [override_row] + channel_rows + custom_data_rows

        return sg.Frame(
            f"Target {target_id}", frame_layout, key=f"-TARGET-FRAME-{target_id}-"
        )

    def _update_target_frames(self):
        """Update the UI with frames for each target"""
        # Get list of target IDs
        target_ids = self.manager.get_target_ids()

        if not target_ids:
            # No targets available
            self.window["-TARGETS-COLUMN-"].update(visible=True)
            self.window["-TARGETS-COLUMN-"].Widget.configure(bg="lightgray")
            self.window["-TARGETS-COLUMN-"].layout(
                [[sg.Text("No targets detected yet")]]
            )
            return

        # Create or update target frames
        target_frames = []
        for target_id in target_ids:
            target_frames.append([self._create_target_frame(target_id)])

        # Update layout
        self.window["-TARGETS-COLUMN-"].update(visible=False)
        self.window["-TARGETS-COLUMN-"].Widget.configure(bg="lightgray")
        self.window["-TARGETS-COLUMN-"].layout(target_frames)
        self.window["-TARGETS-COLUMN-"].update(visible=True)

        # Update status
        self.window["-STATUS-"].update(
            f"Connected to {self.manager.port}. {len(target_ids)} targets detected."
        )

    def _update_target_display(self, target_id: int):
        """Update display for a specific target"""
        target = self.manager.get_target(target_id)
        if not target:
            return

        # Update basic target info
        self.window[f"-TARGET-{target_id}-STATUS-"].update(target.status)
        self.window[f"-TARGET-{target_id}-SIGNAL-"].update(f"{target.signal_strength}")
        self.window[f"-TARGET-{target_id}-BATT-"].update(
            f"{target.battery_voltage:.2f}V"
        )

        # Update custom data fields
        for key, value in target.custom_data.items():
            if self.window.find_element(
                f"-TARGET-{target_id}-DATA-{key}-", silent_on_error=True
            ):
                self.window[f"-TARGET-{target_id}-DATA-{key}-"].update(f"{value}")

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
        # Schedule GUI update in the main thread
        self.window.write_event_value("-TARGET-UPDATE-", data)

    def _on_error(self, error_msg: str):
        """Callback when an error occurs"""
        # Schedule GUI update in the main thread
        self.window.write_event_value("-ERROR-", error_msg)

    def _handle_channel_change(self, target_id: int, ch_num: int, value: str):
        """Handle changes to channel values"""
        target = self.manager.get_target(target_id)
        if not target:
            return

        channel = target.channels.get(ch_num)
        if not channel:
            return

        # Update channel state based on selected value
        if value == "DEFAULT":
            channel.state = ChannelState.DEFAULT
        elif value == channel.states[0]:
            channel.state = ChannelState.STATE_1
        elif value == channel.states[1]:
            channel.state = ChannelState.STATE_2
        elif channel.has_third_state and value == channel.states[2]:
            channel.state = ChannelState.STATE_3

        # Send updated data to transmitter
        self.manager.send_target_override(target)

    def run(self):
        """Main event loop"""
        while True:
            event, values = self.window.read(timeout=REFRESH_RATE_MS)

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
                data = values[event]

                if "type" in data:
                    if data["type"] == "target_update" and "target_id" in data:
                        target_id = data["target_id"]

                        # Check if we need to recreate all frames (new target)
                        if target_id not in self.target_frames:
                            self._update_target_frames()
                        else:
                            # Just update this target's display
                            self._update_target_display(target_id)

                    elif data["type"] == "tx_channels" and "data" in data:
                        self._update_tx_channels_display(data["data"])

            elif event == "-ERROR-":
                error_msg = values[event]
                self.window["-STATUS-"].update(f"ERROR: {error_msg}")

            elif event == "-CLEAR-LOG-":
                self.window["-STATUS-"].update("Status cleared")

            # Handle override toggle events
            elif event.startswith("-TARGET-") and event.endswith("-OVERRIDE-"):
                parts = event.split("-")
                target_id = int(parts[2])
                
                target = self.manager.get_target(target_id)
                if target:
                    # Toggle the override state
                    target.override_enabled = not target.override_enabled
                    
                    # Update button text
                    btn_text = "Override: ON" if target.override_enabled else "Override: OFF"
                    self.window[event].update(text=btn_text)
                    
                    # Enable/disable channel controls
                    for ch_num in target.channels:
                        self.window[f"-TARGET-{target_id}-CH-{ch_num}-"].update(
                            disabled=not target.override_enabled
                        )

                    # Send updated data
                    self.manager.send_target_override(target)

            # Handle channel change events
            elif event.startswith("-TARGET-") and "-CH-" in event:
                parts = event.split("-")
                target_id = int(parts[2])
                ch_num = int(parts[4])
                self._handle_channel_change(target_id, ch_num, values[event])

        # Clean up
        self.manager.disconnect()
        self.window.close()


if __name__ == "__main__":
    app = ESPKenisisUI()
    app.run()
