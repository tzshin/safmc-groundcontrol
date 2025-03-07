"""
Main UI window for the ESPKenisis Radio Link Manager.
"""

import PySimpleGUI as sg
import json
import time
from typing import Dict, List
from config import THEME, WINDOW_TITLE, REFRESH_RATE_MS, debug_print
from core.manager import ESPKenisisManager
from models.channel import ChannelState


class ESPKenisisUI:
    """Main UI class for the ESPKenisis Radio Link Manager"""

    def __init__(self):
        self.window = None
        self.manager = ESPKenisisManager(
            callback_on_targets_update=self._on_targets_update,
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
        # Check if we already have targets (possible on window recreation)
        target_ids = self.manager.get_target_ids()

        if target_ids:
            self.targets_layout = []
            self.target_frames = {}

            # Create a frame for each existing target
            for target_id in target_ids:
                try:
                    frame = self._create_target_frame(target_id)
                    self.targets_layout.append([frame])
                    self.target_frames[target_id] = frame
                except Exception as e:
                    debug_print(f"Error creating frame for target {target_id}: {e}")
        else:
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
        status_text = "Not connected"
        if self.manager.is_connected:
            status_text = (
                f"Connected to {self.manager.port}. {len(target_ids)} targets detected."
            )

        status_layout = [
            [
                sg.Text(
                    status_text,
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

        # Prevent updates if we don't have a window yet
        if self.window is None:
            debug_print("Window does not exist yet, setting frame_update_pending")
            self.frame_update_pending = True
            return False

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

    def _on_targets_update(self, data: Dict):
        """Callback when target data is updated"""
        try:
            # We're using thread-safe communication via event values
            debug_print(f"Target update callback with data: {data}")

            # Safety check - only proceed if window exists and is initialized
            if self.window is None:
                debug_print("Window is None, scheduling update after window creation")
                self.frame_update_pending = True
                return

            # For sanity checking - only trigger UI updates for major changes
            # This helps prevent the window from constantly reopening

            # Only send UI updates for specific event types
            include_event = False

            if "type" in data:
                # For target updates, only include if we know it's relevant
                if data["type"] == "targets_update" and "target_id" in data:
                    target_id = data["target_id"]

                    # Always include new targets we haven't seen before
                    if target_id not in self.target_frames:
                        include_event = True
                    # For existing targets, we'll update less frequently (in the event handler)

            if include_event:
                try:
                    # Convert the data to a serializable format first
                    serialized_data = json.dumps(data)

                    # Use this to signal the main thread to update the UI
                    debug_print(
                        f"Posting event to main thread: {serialized_data[:100]}..."
                    )
                    self.window.write_event_value("-TARGET-UPDATE-", serialized_data)
                except Exception as e:
                    debug_print(f"Error posting event to main thread: {e}")
                    # Schedule a frame update instead of crashing
                    self.frame_update_pending = True
            else:
                debug_print(
                    f"Skipping UI update for this event type: {data.get('type', 'unknown')}"
                )

        except Exception as e:
            print(f"Error in _on_targets_update: {e}")
            debug_print(f"Target update callback error: {e}")

            # Instead of trying complex recovery, just schedule a frame rebuild
            self.frame_update_pending = True

    def _on_error(self, error_msg: str):
        """Callback when an error occurs"""
        try:
            # Safety check - only proceed if window exists
            if self.window is None:
                print(f"Error occurred before window creation: {error_msg}")
                return

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
                        if data["type"] == "targets_update":
                            # Handle both formats - single target_id or list of targets
                            if "target_id" in data:
                                # Old format - single target update
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
                                    debug_print(
                                        f"Target {target_id} not found in manager"
                                    )

                            elif "targets" in data:
                                # New format - multiple targets update
                                target_ids = data["targets"]
                                debug_print(
                                    f"Received update for multiple targets: {target_ids}"
                                )

                                # Check if any of these targets are not yet displayed
                                new_targets = False
                                for target_id in target_ids:
                                    if target_id not in self.target_frames:
                                        new_targets = True
                                        break

                                if new_targets:
                                    debug_print(
                                        "At least one new target found, scheduling UI update"
                                    )
                                    self.frame_update_pending = True
                                    # Force an immediate refresh if we haven't updated in a while
                                    if (time.time() - self.last_frame_update) > 5.0:
                                        self._update_target_frames()
                                else:
                                    debug_print(
                                        "All targets already displayed, no update needed"
                                    )

                        elif data["type"] == "tx_channels" and "data" in data:
                            debug_print(f"Updating TX channels: {data['data']}")
                            # This is a simpler update that doesn't involve target frames
                            self._update_tx_channels_display(data["data"])

                        # Channel data is now included directly in targets_update

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
