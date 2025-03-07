"""
Channel model for the ESPKenisis Radio Link Manager.
"""

from enum import Enum, auto
from dataclasses import dataclass, field
from typing import List, Optional, Union, Literal, Dict


class ChannelState(Enum):
    """Enum for channel states"""

    DEFAULT = auto()  # No override
    STATE_1 = auto()  # First state
    STATE_2 = auto()  # Second state
    STATE_3 = auto()  # Third state (only for flight mode)


class ChannelType(Enum):
    """Enum for channel types"""
    
    CONTINUOUS = auto()  # Continuous value channel (like AETR)
    DISCRETE = auto()    # Discrete state channel (like arm, kill switches)


@dataclass
class Channel:
    """Base class representing a generic channel"""

    name: str
    channel_number: int
    value: int = 0  # Actual value sent to the target
    channel_type: ChannelType = ChannelType.DISCRETE
    
    def get_state_text(self) -> str:
        """Get the text representation of the current state"""
        return "DEFAULT"
        
    def set_value(self, value: int) -> None:
        """Set the raw value for the channel"""
        self.value = value
        
    def get_value(self) -> int:
        """Get the current raw value"""
        return self.value


@dataclass
class ContinuousChannel(Channel):
    """Channel with continuous value range (like AETR)"""
    
    min_value: int = 0
    max_value: int = 1000
    default_value: int = 500
    override_value: Optional[int] = None
    
    def __post_init__(self):
        self.channel_type = ChannelType.CONTINUOUS
        self.value = self.default_value
    
    def get_state_text(self) -> str:
        """Get the text representation of the current state"""
        if self.override_value is None:
            return f"DEFAULT ({self.value})"
        return f"OVERRIDE ({self.value})"
    
    def reset_to_default(self) -> None:
        """Reset to default value"""
        self.override_value = None
        self.value = self.default_value
        
    def set_override(self, value: int) -> None:
        """Set override value"""
        # Clamp value to valid range
        clamped_value = max(self.min_value, min(value, self.max_value))
        self.override_value = clamped_value
        self.value = clamped_value


@dataclass
class DiscreteChannel(Channel):
    """Channel with discrete states (like switches)"""

    states: List[str]
    state: ChannelState = ChannelState.DEFAULT
    state_values: Dict[ChannelState, int] = field(default_factory=dict)
    
    def __post_init__(self):
        self.channel_type = ChannelType.DISCRETE
        # Initialize default state values if not provided
        if not self.state_values:
            # For typical PWM values: DEFAULT=no override, STATE_1=1000, STATE_2=1500, STATE_3=2000
            self.state_values = {
                ChannelState.DEFAULT: 0,      # No override
                ChannelState.STATE_1: 1000,   # First state
                ChannelState.STATE_2: 1500,   # Second state
                ChannelState.STATE_3: 2000,   # Third state
            }
    
    def get_state_text(self) -> str:
        """Get the text representation of the current state"""
        if self.state == ChannelState.DEFAULT:
            return "DEFAULT"
        elif self.state == ChannelState.STATE_1 and len(self.states) > 0:
            return self.states[0]
        elif self.state == ChannelState.STATE_2 and len(self.states) > 1:
            return self.states[1]
        elif self.state == ChannelState.STATE_3 and len(self.states) > 2:
            return self.states[2]
        return "UNKNOWN"
        
    def set_state(self, state: ChannelState) -> None:
        """Set the state for the channel"""
        if state in self.state_values:
            self.state = state
            self.value = self.state_values[state]
            
    def get_available_states(self) -> List[str]:
        """Get the list of available states"""
        return self.states
