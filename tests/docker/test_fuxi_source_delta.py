"""Verify the prepared source-only target without Docker builds or a model."""
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
import subprocess


REPO = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "source_delta", REPO / "docker/source-delta-verify.py")
DELTA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DELTA)
PARENT = "sha256:" + "a" * 64
REVISION = "b" * 40


class SourceDeltaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.records = []
        for member in DELTA.MEMBERS:
            path = self.root / member
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("synthetic source\n", encoding="utf-8")
            self.records.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {member}")
        self.manifest = self.root / ".fuxi-source-delta.sha256"
        self.save_manifest(self.records)

    def save_manifest(self, records):
        self.manifest.write_text("\n".join(records) + "\n", encoding="utf-8")

    def test_valid_exact_members_record_partial_source_provenance(self):
        result = DELTA.verify_source(self.root, PARENT, REVISION)
        self.assertEqual(result["parent_image_id"], PARENT)
        self.assertEqual(result["delta_source_revision"], REVISION)
        self.assertEqual(set(result["members"]), set(DELTA.MEMBERS))
        self.assertFalse((self.root / ".hermes-git-sha").exists())
        self.assertFalse((self.root / ".fuxi-source-delta.json").exists())

    def test_missing_extra_duplicate_and_traversal_members_fail_closed(self):
        cases = [self.records[:-1], self.records + [self.records[0]],
                 self.records + ["a" * 64 + "  .env"],
                 self.records + ["a" * 64 + "  ../outside"]]
        for records in cases:
            with self.subTest(records=records):
                self.save_manifest(records)
                with self.assertRaises(ValueError):
                    DELTA.verify_source(self.root, PARENT, REVISION)

    def test_modified_or_missing_source_is_rejected(self):
        member = self.root / DELTA.MEMBERS[0]
        member.write_text("changed", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "checksum"):
            DELTA.verify_source(self.root, PARENT, REVISION)
        member.unlink()
        with self.assertRaises(ValueError):
            DELTA.verify_source(self.root, PARENT, REVISION)

    def test_symlink_manifest_and_symlink_source_are_rejected(self):
        outside = self.root / "outside"
        outside.write_text("synthetic source\n", encoding="utf-8")
        member = self.root / DELTA.MEMBERS[0]
        member.unlink()
        member.symlink_to(outside)
        with self.assertRaises(ValueError):
            DELTA.verify_source(self.root, PARENT, REVISION)
        member.unlink()
        member.write_text("synthetic source\n", encoding="utf-8")
        self.manifest.unlink()
        self.manifest.symlink_to(outside)
        with self.assertRaises(ValueError):
            DELTA.verify_source(self.root, PARENT, REVISION)

    def test_unpinned_parent_or_non_commit_revision_is_rejected(self):
        for parent, revision in [("latest", REVISION), (PARENT, "main"),
                                 ("sha256:" + "z" * 64, REVISION)]:
            with self.subTest(parent=parent, revision=revision):
                with self.assertRaises(ValueError):
                    DELTA.verify_source(self.root, parent, revision)

    def test_malformed_or_oversized_manifest_is_rejected(self):
        for value in ["invalid\n", "x" * 4097]:
            self.manifest.write_text(value, encoding="utf-8")
            with self.assertRaises(ValueError):
                DELTA.verify_source(self.root, PARENT, REVISION)

    def test_oversized_source_is_rejected_before_reading_it(self):
        member = self.root / DELTA.MEMBERS[0]
        with member.open("wb") as handle:
            handle.truncate(16 * 1024 * 1024 + 1)
        with self.assertRaisesRegex(ValueError, "too_large"):
            DELTA.verify_source(self.root, PARENT, REVISION)

    def test_delta_target_contains_only_fixed_copies_and_one_verifier(self):
        target = (REPO / "docker/Dockerfile.source-delta").read_text(encoding="utf-8")
        self.assertIn("AS fuxi_source_delta", target)
        copies = {line for line in target.splitlines() if line.startswith("COPY ")}
        expected = {f"COPY {member} /opt/hermes/{member}" for member in DELTA.MEMBERS}
        expected.add("COPY .fuxi-source-delta.sha256 /opt/hermes/.fuxi-source-delta.sha256")
        self.assertEqual(copies, expected)
        self.assertEqual(sum(line.startswith("RUN ") for line in target.splitlines()), 1)
        for forbidden in ("pip install", "uv sync", "npm ci", "apt-get", "curl ",
                          "wget ", "git clone", "COPY . ", "ENTRYPOINT", "CMD ",
                          "USER ", "ENV ", "WORKDIR"):
            self.assertNotIn(forbidden, target)

    def committed_fixture(self):
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / "docker/Dockerfile.source-delta").write_text("synthetic Dockerfile\n", encoding="utf-8")
        (self.root / ".dockerignore").write_text("*.md\n", encoding="utf-8")
        (self.root / ".env").write_text("synthetic secret, never export\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.root), "-c", "user.name=Fixture",
                        "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture"],
                       check=True)
        return subprocess.check_output(["git", "-C", str(self.root), "rev-parse", "HEAD"],
                                       text=True).strip()

    def test_preparation_exports_only_committed_members_and_keeps_skill_visible(self):
        revision = self.committed_fixture()
        (self.root / DELTA.MEMBERS[0]).write_text("uncommitted change", encoding="utf-8")
        destination = self.root / "prepared"
        DELTA.prepare_source(self.root, revision, destination)
        self.assertEqual((destination / DELTA.MEMBERS[0]).read_text(), "synthetic source\n")
        expected = set(DELTA.MEMBERS) | {"Dockerfile", ".fuxi-source-delta.sha256"}
        actual = {p.relative_to(destination).as_posix() for p in destination.rglob("*") if p.is_file()}
        self.assertEqual(actual, expected)
        DELTA.verify_source(destination, PARENT, revision)
        with self.assertRaises(FileExistsError):
            DELTA.prepare_source(self.root, revision, destination)

    def test_preparation_rejects_git_symlink_before_creating_output(self):
        member = self.root / DELTA.MEMBERS[0]
        member.unlink()
        member.symlink_to("outside")
        revision = self.committed_fixture()
        destination = self.root / "prepared"
        with self.assertRaisesRegex(ValueError, "git_member"):
            DELTA.prepare_source(self.root, revision, destination)
        self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
