"""Verify exact maintained source members; never install or contact a provider."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys


MEMBERS = (
    "agent/conversation_loop.py",
    "agent/tool_executor.py",
    "agent/turn_finalizer.py",
    "hermes_cli/middleware.py",
    "gateway/platforms/api_server.py",
    "skills/research/llm-wiki-fuxi/SKILL.md",
    "skills/research/llm-wiki-fuxi/references/continuation.md",
    "docker/source-delta-verify.py",
)


def prepare_source(repo: Path, revision: str, destination: Path) -> None:
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("source_delta_revision_not_pinned")
    resolved = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", f"{revision}^{{commit}}"], text=True).strip()
    if resolved != revision:
        raise ValueError("source_delta_revision_not_pinned")
    payload = {}
    for member in (*MEMBERS, "docker/Dockerfile.source-delta"):
        mode = subprocess.check_output(
            ["git", "-C", str(repo), "ls-tree", revision, "--", member], text=True)
        if not mode.startswith("100644 blob "):
            raise ValueError("source_delta_git_member_invalid")
        content = subprocess.check_output(["git", "-C", str(repo), "show", f"{revision}:{member}"])
        if len(content) > 16 * 1024 * 1024:
            raise ValueError("source_delta_member_too_large")
        payload[member] = content
    # Export only committed members; intentionally omit the full build's *.md exclusion.
    destination.mkdir(parents=False, exist_ok=False)
    for member, content in payload.items():
        path = destination / ("Dockerfile" if member == "docker/Dockerfile.source-delta" else member)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    manifest = "".join(f"{hashlib.sha256(payload[member]).hexdigest()}  {member}\n"
                       for member in MEMBERS)
    (destination / ".fuxi-source-delta.sha256").write_text(manifest, encoding="utf-8")


def verify_source(root: Path, parent_image_id: str, revision: str) -> dict:
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", parent_image_id):
        raise ValueError("source_delta_parent_not_pinned")
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("source_delta_revision_not_pinned")
    root = root.resolve(strict=True)
    manifest = root / ".fuxi-source-delta.sha256"
    if manifest.is_symlink() or not manifest.is_file() or manifest.stat().st_size > 4096:
        raise ValueError("source_delta_manifest_invalid")
    expected = {}
    for line in manifest.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if not match or match[2] not in MEMBERS or match[2] in expected:
            raise ValueError("source_delta_manifest_member_invalid")
        expected[match[2]] = match[1]
    if set(expected) != set(MEMBERS):
        raise ValueError("source_delta_manifest_members_incomplete")
    for member, sha256 in expected.items():
        path = root / member
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
            raise ValueError("source_delta_member_path_invalid")
        if path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError("source_delta_member_too_large")
        if hashlib.sha256(path.read_bytes()).hexdigest() != sha256:
            raise ValueError("source_delta_member_checksum_mismatch")
    return {"schema": "fuxi.hermes-source-delta/v1", "parent_image_id": parent_image_id,
            "delta_source_revision": revision, "members": expected}


def main(argv: list[str]) -> None:
    if len(argv) == 4 and argv[0] == "--prepare":
        prepare_source(Path(argv[1]), argv[2], Path(argv[3]))
        return
    if len(argv) != 2:
        raise ValueError("source_delta_identity_required")
    root = Path("/opt/hermes")
    receipt = verify_source(root, argv[0], argv[1])
    target = root / ".fuxi-source-delta.json"
    if target.is_symlink():
        raise ValueError("source_delta_receipt_path_invalid")
    target.write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1:])
