import unittest

from parking_system import ParkingLot


class ParkingLotTests(unittest.TestCase):
    def setUp(self):
        self.lot = ParkingLot(3)

    def test_park_vehicle_assigns_next_available_slot(self):
        self.assertEqual(self.lot.park_vehicle("CAR-101"), 1)
        self.assertEqual(self.lot.park_vehicle("CAR-202"), 2)

    def test_park_vehicle_raises_when_lot_is_full(self):
        self.lot.park_vehicle("CAR-101")
        self.lot.park_vehicle("CAR-202")
        self.lot.park_vehicle("CAR-303")

        with self.assertRaises(ValueError):
            self.lot.park_vehicle("CAR-404")

    def test_leave_vehicle_releases_slot(self):
        self.lot.park_vehicle("CAR-101")

        vehicle = self.lot.leave_vehicle(1)

        self.assertEqual(vehicle, "CAR-101")
        self.assertEqual(self.lot.available_slots, [1, 2, 3])

    def test_get_status_lists_parked_vehicles(self):
        self.lot.park_vehicle("CAR-101")

        status = self.lot.get_status()

        self.assertEqual(status["available_slots"], [2, 3])
        self.assertEqual(status["occupied_slots"][1], "CAR-101")


if __name__ == "__main__":
    unittest.main()
