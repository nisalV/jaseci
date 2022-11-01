from jaseci.utils.test_core import CoreTest


class StdLibTest(CoreTest):

    fixture_src = __file__

    def test_clear_report(self):
        ret = self.call(
            self.mast,
            ["sentinel_register", {"code": self.load_jac("general.jac")}],
        )
        ret = self.call(self.mast, ["walker_run", {"name": "report_clearing"}])
        self.assertEqual(len(ret["report"]), 1)
        self.assertEqual(ret["report"][0], 7)

    def test_internal_lib(self):
        ret = self.call(
            self.mast,
            ["sentinel_register", {"code": self.load_jac("general.jac")}],
        )
        ret = self.call(self.mast, ["walker_run", {"name": "internal_lib"}])
        self.assertEqual(len(ret["report"]), 1)
        self.assertTrue(
            ret["report"][0].startswith("ncalls,tottime,percall,cumtime,percall,")
        )