import concurrent.futures
from pathlib import Path
import tempfile
import unittest

from gateway.platforms.api_run_store import APIRunStore, RunIdentityConflict, detached_run_status


class RunStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = APIRunStore(self.temp.name)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_reopen_reuses_identity_without_reexecution(self):
        created, first = self.store.claim("attempt", {"input": "synthetic"})
        self.assertTrue(created)
        other = APIRunStore(self.temp.name)
        try:
            created, same = other.claim("attempt", {"input": "synthetic"})
            self.assertFalse(created)
            self.assertEqual(first["run_id"], same["run_id"])
            self.assertEqual(detached_run_status(same)["status"], "unknown")
            self.assertTrue(detached_run_status(same)["reconciliation_required"])
        finally:
            other.close()

    def test_changed_payload_conflicts(self):
        self.store.claim("attempt", {"input": "one"})
        with self.assertRaises(RunIdentityConflict):
            self.store.claim("attempt", {"input": "two"})

    def test_key_lookup_never_creates_a_run(self):
        self.assertIsNone(self.store.get_by_key("missing"))
        created, first = self.store.claim("missing", {"input": "synthetic"})
        self.assertTrue(created)
        self.assertEqual(self.store.get_by_key("missing")["run_id"], first["run_id"])
        self.assertIsNone(self.store.get_by_key("different"))

    def test_key_lookup_survives_restart_without_claim(self):
        _, first = self.store.claim("lookup", {})
        other = APIRunStore(self.temp.name)
        try:
            self.assertEqual(other.get_by_key("lookup")["run_id"], first["run_id"])
        finally:
            other.close()

    def test_profile_stores_are_isolated(self):
        _, first = self.store.claim("attempt", {})
        other = APIRunStore(Path(self.temp.name) / "other-profile")
        try:
            self.assertIsNone(other.get(first["run_id"]))
            created, second = other.claim("attempt", {})
            self.assertTrue(created)
            self.assertNotEqual(first["run_id"], second["run_id"])
        finally:
            other.close()

    def test_terminal_status_survives_restart(self):
        _, first = self.store.claim("attempt", {})
        self.store.update({**first, "status": "completed", "usage": {"input_tokens": 7}})
        other = APIRunStore(self.temp.name)
        try:
            status = detached_run_status(other.get(first["run_id"]))
            self.assertEqual(status["status"], "completed")
            self.assertEqual(status["usage"]["input_tokens"], 7)
        finally:
            other.close()

    def test_concurrent_claim_only_one_creator(self):
        def claim(_):
            store = APIRunStore(self.temp.name)
            try:
                return store.claim("attempt", {"input": "same"})
            finally:
                store.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(claim, range(8)))
        self.assertEqual(sum(created for created, _ in results), 1)
        self.assertEqual(len({status["run_id"] for _, status in results}), 1)

    def test_no_in_memory_fallback(self):
        file = Path(self.temp.name) / "not-a-directory"
        file.touch()
        with self.assertRaises(OSError):
            APIRunStore(file)

    def test_unknown_id_cannot_update(self):
        with self.assertRaisesRegex(RuntimeError, "run_identity_missing"):
            self.store.update({"run_id": "unclaimed", "status": "completed"})

    def test_yield_survives_restart_and_resume_is_consumed_once(self):
        _, parent = self.store.claim("parent", {})
        parent.update(status="paused", continuation={"checkpointSha256": "abc"})
        self.store.update(parent)
        resumed = {"run_id": parent["run_id"], "checkpoint_sha256": "abc"}
        created, child = self.store.claim("child", {"resume": parent["run_id"]}, resume_from=resumed)
        self.assertTrue(created)
        self.assertEqual(child["resumed_from_run_id"], parent["run_id"])
        created, same = self.store.claim("child", {"resume": parent["run_id"]}, resume_from=resumed)
        self.assertFalse(created)
        self.assertEqual(same["run_id"], child["run_id"])
        with self.assertRaisesRegex(RunIdentityConflict, "run_resume_conflict"):
            self.store.claim("second-child", {}, resume_from=resumed)
        other = APIRunStore(self.temp.name)
        try:
            record = detached_run_status(other.get(parent["run_id"]))
            self.assertEqual(record["status"], "paused")
            self.assertEqual(record["resumed_by_run_id"], child["run_id"])
        finally:
            other.close()

    def test_resume_rejects_unknown_failed_and_changed_checkpoint(self):
        _, parent = self.store.claim("parent", {})
        resumed = {"run_id": parent["run_id"], "checkpoint_sha256": "abc"}
        for state in ("accepted", "running", "unknown", "failed", "cancelled", "completed"):
            self.store.update({**parent, "status": state, "continuation": {"checkpointSha256": "abc"}})
            with self.subTest(state=state), self.assertRaises(RunIdentityConflict):
                self.store.claim("child", {}, resume_from=resumed)
        self.store.update({**parent, "status": "paused", "continuation": {"checkpointSha256": "wrong"}})
        with self.assertRaises(RunIdentityConflict):
            self.store.claim("child", {}, resume_from=resumed)
        self.assertIsNone(self.store.get_by_key("child"))

    def test_resume_concurrency_does_not_purchase_two_native_runs(self):
        _, parent = self.store.claim("parent", {})
        self.store.update({**parent, "status": "paused", "continuation": {"checkpointSha256": "abc"}})
        def claim(index):
            store = APIRunStore(self.temp.name)
            try:
                return store.claim(str(index), {}, resume_from={"run_id": parent["run_id"],
                                   "checkpoint_sha256": "abc"})[0]
            except RunIdentityConflict:
                return False
            finally:
                store.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(sum(pool.map(claim, range(8))), 1)

    def test_payload_checked_lookup_precedes_resume_history_validation(self):
        self.store.claim("lookup", {"identity": "first"})
        self.assertIsNotNone(self.store.get_by_key("lookup", {"identity": "first"}))
        with self.assertRaises(RunIdentityConflict):
            self.store.get_by_key("lookup", {"identity": "other"})


if __name__ == "__main__":
    unittest.main()
