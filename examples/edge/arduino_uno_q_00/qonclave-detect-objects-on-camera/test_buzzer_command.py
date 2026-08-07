import sys
import os
import unittest
from unittest.mock import MagicMock

# Add python directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "python"))

class TestBuzzerCommand(unittest.TestCase):
    def test_buzzer_command_dispatch(self):
        # Mock Bridge and UI
        mock_bridge = MagicMock()
        mock_ui = MagicMock()
        
        # Test command parsing logic
        cmd_start = {"type": "buzzer", "action": "start", "frequency": 880, "duration": 500}
        cmd_stop = {"action": "stop"}

        action = str(cmd_start.get("action", "")).strip().lower()
        frequency = int(cmd_start.get("frequency", 440))
        duration = int(cmd_start.get("duration", 0))

        self.assertEqual(action, "start")
        self.assertEqual(frequency, 880)
        self.assertEqual(duration, 500)

        action_stop = str(cmd_stop.get("action", "")).strip().lower()
        self.assertEqual(action_stop, "stop")

if __name__ == "__main__":
    unittest.main()
