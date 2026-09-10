import assert from 'node:assert/strict';
import { test } from 'node:test';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';

// Execute the actual inline gate, rather than a second implementation of it.
const workflow = fs.readFileSync(new URL('../.github/workflows/ci.yml', import.meta.url), 'utf8');
const block = workflow.split('  all-checks-pass:')[1]?.split('  ci-timings:')[0];
const match = block?.match(/echo "\$NEEDS" \| python3 -c "\n([\s\S]*?)\n          "/);
assert.ok(match, 'CI gate extraction must fail if workflow shape changes');
const program = match[1].replace(/^          /gm, '').replaceAll('\\"', '"');

for (const result of ['success', 'failure', 'cancelled', 'skipped', 'missing']) {
  test(`Wiki 必检状态 ${result}`, t => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), 'wiki-ci-'));
    t.after(() => fs.rmSync(root, {recursive: true, force: true}));
    const output = path.join(root, 'output');
    const needs = {tests: {result: 'skipped'}};
    if (result !== 'missing') needs['fuxi-wiki-upstream'] = {result};
    const run = spawnSync('python3', ['-c', program.replaceAll('$GITHUB_OUTPUT', output)], {
      input: JSON.stringify(needs), encoding: 'utf8',
    });
    assert.equal(run.status, result === 'success' ? 0 : 1, run.stderr || run.stdout);
    assert.ok(fs.readFileSync(output, 'utf8').startsWith('needs-json='));
  });
}
