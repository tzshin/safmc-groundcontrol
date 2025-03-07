"""
ESPKenisis Manager for handling serial communication with the transmitter.
"""
import serial
import serial.tools.list_ports
import threading
import time
import json
from typing import Dict, List, Optional, Callable
from config import debug_print
from models.target import Target


class ESPKenisisManager:
    """Manages the serial connection to the ESPKenisis transmitter"""

    def __init__(
        self,
        callback_on_targets_update: Callable[[Dict], None],
        callback_on_error: Callable[[str], None],
    ):
        self.port = None
        self.serial_conn = None
        self.is_connected = False
        self.is_running = False
        self.read_thread = None
        self.targets = {}  # Dictionary of targets indexed by target_id
        self.callback_on_targets_update = callback_on_targets_update
        self.callback_on_error = callback_on_error
        self.raw_data_handlers = {}  # Dictionary of message type handlers

        # Register default handlers
        self._register_default_handlers()

    def _register_default_handlers(self):
        """Register default handlers for different message types"""
        self.register_handler("targets_update", self._handle_targets_update)
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
            self.callback_on_targets_update(data)

    def _handle_targets_update(self, data: Dict):
        """Handle target state messages"""
        if "targets" not in data:
            debug_print("Received targets_update message without 'targets' field")
            return

        targets_data = data["targets"]
        debug_print(f"Processing {len(targets_data)} targets from update")

        # Track which targets were updated to notify UI once at the end
        updated_targets = []

        for target_data in targets_data:
            if "id" not in target_data:
                debug_print("Target data missing 'id' field:", target_data)
                continue

            target_id = target_data["id"]
            debug_print(f"Processing target ID {target_id}")

            # Create target if doesn't exist
            if target_id not in self.targets:
                debug_print(f"Creating new target with ID {target_id}")
                self.targets[target_id] = Target(id=target_id)

            # Update target with received data
            target = self.targets[target_id]
            target.update_from_data(target_data)
            updated_targets.append(target_id)
            debug_print(
                f"Updated target {target_id}: status={target.status}, signal={target.signal_strength}, battery={target.battery_voltage}"
            )

        # Send a single update with all changed targets
        if updated_targets:
            debug_print(f"Notifying UI of update for {len(updated_targets)} targets")
            self.callback_on_targets_update(
                {"type": "targets_update", "targets": updated_targets}
            )


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