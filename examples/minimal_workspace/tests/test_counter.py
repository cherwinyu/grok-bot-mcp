import unittest

from src.counter import count_above


class CounterTest(unittest.TestCase):
    def test_count(self):
        self.assertEqual(count_above([1, 3, 5], 2), 2)


if __name__ == "__main__":
    unittest.main()
