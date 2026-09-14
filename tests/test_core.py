import unittest
from ue2godot.core.result import Result
from ue2godot.core.ids import stable_id, unique_filename_for_path
from ue2godot.core.config import ResolvedConfig, deep_merge, compute_config_hash
from ue2godot.core.registry import AssetRegistry

class TestCore(unittest.TestCase):
    def test_result(self):
        r = Result.ok(10)
        self.assertTrue(r.is_ok)
        self.assertEqual(r.value, 10)

        err = Result.err("failed")
        self.assertTrue(err.is_err)
        self.assertEqual(err.error, "failed")

    def test_ids(self):
        sid = stable_id("MESH", "/Game/TestMesh")
        self.assertTrue(sid.startswith("MESH_"))

        fn = unique_filename_for_path("/Game/TestMesh.TestMesh")
        self.assertTrue(fn.endswith(".glb"))

    def test_config(self):
        base = {"a": 1, "b": {"c": 2}}
        override = {"b": {"c": 3, "d": 4}}
        merged = deep_merge(base, override)
        self.assertEqual(merged["b"]["c"], 3)
        self.assertEqual(merged["b"]["d"], 4)

        res = ResolvedConfig.resolve(base, override, {})
        self.assertIsNotNone(res.config_hash)

if __name__ == "__main__":
    unittest.main()
