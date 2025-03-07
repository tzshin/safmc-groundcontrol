"""
Target model for the ESPKenisis Radio Link Manager.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Union
import time
from models.channel import Channel, ChannelState, ChannelType, ContinuousChannel, DiscreteChannel

@dataclass
class Target:
    """Class representing a target (RX) with its channels and status"""

    id: int
    mac: bytearray = field(default_factory=lambda: bytearray(6))
    channels: Dict[int, Union[ContinuousChannel, DiscreteChannel]] = field(default_factory=dict)
    connection_state = bool
    last_successful_send = int
    name = str
    channels_overridden = bool
    override_timeout_remaining = float

    override_enabled: bool = False
    last_update_time: float = 0
    signal_strength: int = 0
    battery_voltage: float = 0.0
    status: str = "Unknown"
    custom_data: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """Initialize the channels after class instantiation"""
        if not self.channels:
            # Initialize continuous AETR channels (1-4)
            self.channels = {
                1: ContinuousChannel("Aileron", 1, min_value=1000, max_value=2000, default_value=1500),
                2: ContinuousChannel("Elevator", 2, min_value=1000, max_value=2000, default_value=1500),
                3: ContinuousChannel("Throttle", 3, min_value=1000, max_value=2000, default_value=1000),
                4: ContinuousChannel("Rudder", 4, min_value=1000, max_value=2000, default_value=1500),
                
                # Initialize discrete state channels (5-8)
                5: DiscreteChannel("Arm", 5, ["ARMED", "DISARMED"]),
                6: DiscreteChannel("Kill", 6, ["KILLED", "ACTIVE"]),
                7: DiscreteChannel("Flight Mode", 7, ["MANUAL", "POSITION", "MISSION"]),
                8: DiscreteChannel("Offboard", 8, ["ENABLED", "DISABLED"]),
            }

    def get_override_payload(self) -> Dict:
        """Generate a data payload to send to the transmitter"""
        payload = {
            "id": self.id,
            "override": self.override_enabled,
            "channels": {},
        }

        if self.override_enabled:
            for ch_num, channel in self.channels.items():
                if isinstance(channel, DiscreteChannel) and channel.state != ChannelState.DEFAULT:
                    payload["channels"][str(ch_num)] = channel.state.value
                elif isinstance(channel, ContinuousChannel) and channel.override_value is not None:
                    payload["channels"][str(ch_num)] = channel.value

        return payload

    def update_from_data(self, data: Dict) -> None:
        """Update target from received data packet"""
        self.last_update_time = time.time()

        # Update name if available
        if "name" in data:
            self.custom_data["name"] = data["name"]
            
        # Update mac address if available
        if "mac" in data:
            self.custom_data["mac"] = data["mac"]
            
        # Update connection state if available
        if "connection_state" in data:
            self.custom_data["connection_state"] = data["connection_state"]

        # Update channel values if included - handling list format from target_state
        if "channels" in data and isinstance(data["channels"], list):
            # Handle array format from target_state [6,12,6,6,4,7,4,4]
            channel_array = data["channels"]
            for i, value in enumerate(channel_array, 1):  # Channels typically 1-indexed
                if i in self.channels:
                    self.channels[i].set_value(value)

        # Store all other fields in custom_data for display/future use
        for key, value in data.items():
            if key not in ["id", "channels", "name", "mac", "connection_state", "last_successful_send"]:
                self.custom_data[key] = value
                
    def reset_overrides(self) -> None:
        """Reset all channel overrides"""
        for channel in self.channels.values():
            if isinstance(channel, DiscreteChannel):
                channel.set_state(ChannelState.DEFAULT)
            elif isinstance(channel, ContinuousChannel):
                channel.reset_to_default()
        self.override_enabled = False