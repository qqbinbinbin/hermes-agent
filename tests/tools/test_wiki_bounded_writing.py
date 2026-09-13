"""Execute the maintained skill examples against real, temporary file I/O."""

import json
import re
import tempfile
from pathlib import Path

from tools.environments.local import LocalEnvironment
from tools.file_operations import ShellFileOperations


def test_skill_examples_preserve_one_page(tmp_path):
    resource = Path(__file__).resolve().parents[2] / "skills/research/llm-wiki-fuxi/references/bounded-writing.md"
    calls = [json.loads(block) for block in re.findall(r"```json\s*(.*?)```", resource.read_text(), re.S)]
    ops = ShellFileOperations(LocalEnvironment(cwd=str(tmp_path), timeout=15), cwd=str(tmp_path))
    for call in calls:
        path = str(tmp_path / call["path"])
        if call["tool"] == "write_file":
            ops.write_file(path, call["content"])
        else:
            result = ops.patch_replace(path, call["old_string"], call["new_string"])
            assert result.success, result.to_dict()
    pages = list(tmp_path.rglob("*.md"))
    assert len(pages) == 1
    assert pages[0].read_text() == calls[0]["content"].replace(calls[1]["old_string"], calls[1]["new_string"])


def test_long_list_is_not_limited_to_one_write(tmp_path):
    ops = ShellFileOperations(LocalEnvironment(cwd=str(tmp_path), timeout=15), cwd=str(tmp_path))
    path = tmp_path / "list.md"
    rows = [f"- item-{index:04d}: synthetic-source-{index:04d}\n" for index in range(300)]
    ops.write_file(str(path), "# Synthetic list\n" + "".join(rows[:20]))
    for start in range(20, len(rows), 20):
        anchor = rows[start - 1]
        result = ops.patch_replace(str(path), anchor, anchor + "".join(rows[start:start + 20]))
        assert result.success, result.to_dict()
    expected = "# Synthetic list\n" + "".join(rows)
    assert path.read_text() == expected
    failed = ops.patch_replace(str(path), "ABSENT-ANCHOR-XYZ", "must-not-be-written")
    assert not failed.success
    assert path.read_text() == expected


if __name__ == "__main__":
    for check in (test_skill_examples_preserve_one_page, test_long_list_is_not_limited_to_one_write):
        with tempfile.TemporaryDirectory() as directory:
            check(Path(directory))
        print(f"PASS {check.__name__}")
