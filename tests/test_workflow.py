import unittest

from workflow import WorkflowState, next_state, require_transition


class WorkflowTests(unittest.TestCase):
    def test_confirmed_sequence_allows_only_next_state(self):
        require_transition(WorkflowState.RECEIVED, WorkflowState.FILES_STAGED)
        with self.assertRaisesRegex(ValueError, "invalid workflow transition"):
            require_transition(WorkflowState.RECEIVED, WorkflowState.DESIGN_COMPLETE)

    def test_terminal_state_has_no_successor(self):
        self.assertIsNone(next_state(WorkflowState.GCODE_RELEASED))


if __name__ == "__main__":
    unittest.main()
