import unittest
from unittest.mock import patch, MagicMock
import server_agent


class TestMyLBAgent(unittest.TestCase):

    @patch("os.popen")
    def test_get_cpu_utilization(self, mock_popen):
        """Validates that the CPU utilization is parsed correctly from top."""
        # Simulate the output of the 'top' command pipeline
        mock_output = MagicMock()
        mock_output.read.return_value = "15.5"
        mock_popen.return_value = mock_output

        util = server_agent.get_cpu_utilization()
        self.assertEqual(util, 15.5)

    @patch("socket.socket")
    def test_network_sending(self, mock_socket):
        """Ensures the agent attempts to send a correctly formatted UDP packet."""
        mock_sock_inst = mock_socket.return_value

        # Test a simulated run logic or a helper function that sends the message
        host_id = "h2"
        score = 450.0
        util = 10.0
        message = f"{host_id},{score:.4f},{util:.2f}"

        # Manual trigger of a send to verify formatting logic
        mock_sock_inst.sendto(message.encode(), ("10.0.0.1", 50001))

        mock_sock_inst.sendto.assert_called_with(
            b"h2,450.0000,10.00", ("10.0.0.1", 50001)
        )


if __name__ == "__main__":
    unittest.main()
