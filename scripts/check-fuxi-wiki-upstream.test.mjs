import assert from 'node:assert/strict';
import { test } from 'node:test';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync, spawnSync } from 'node:child_process';
import { checkUpstream } from './check-fuxi-wiki-upstream.mjs';

function fixture(t) {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'wiki-upstream-'));
  t.after(() => fs.rmSync(repo, { recursive: true, force: true }));
  const git = (...args) => execFileSync('git', ['-C', repo, ...args], {encoding: 'utf8'}).trim();
  git('init', '-q');
  git('config', 'user.name', '测试'); git('config', 'user.email', 'test@example.invalid');
  const sourcePath = 'skills/research/llm-wiki';
  fs.mkdirSync(path.join(repo, sourcePath), { recursive: true });
  fs.writeFileSync(path.join(repo, sourcePath, 'SKILL.md'), '# 合成原生技能\n');
  const chinesePath = path.join(repo, 'skills/research/llm-wiki-fuxi/SKILL.md');
  fs.mkdirSync(path.dirname(chinesePath), { recursive: true });
  fs.writeFileSync(chinesePath, '# 独立维护的中文技能\n');
  git('add', '.'); git('commit', '-qm', 'fixture');
  const lockFile = path.join(repo, 'review.json');
  fs.writeFileSync(lockFile, JSON.stringify({sourcePath, reviewedTrees: [{commit: git('rev-parse', 'HEAD'), tree: git('rev-parse', `HEAD:${sourcePath}`), decision: '合成审阅'}]}));
  return {repo, lockFile, git, sourcePath, chinesePath};
}

test('版本变动但技能目录不变，无需重复审阅', t => {
  const f = fixture(t);
  fs.writeFileSync(path.join(f.repo, 'unrelated.txt'), '其他功能');
  f.git('add', 'unrelated.txt'); f.git('commit', '-qm', 'other');
  assert.equal(checkUpstream(f).status, 'reviewed');
});
test('正文变化要求审阅且不改写中文技能或记录', t => {
  const f = fixture(t); const before = fs.readFileSync(f.lockFile, 'utf8');
  const chineseBefore = fs.readFileSync(f.chinesePath);
  fs.writeFileSync(path.join(f.repo, f.sourcePath, 'SKILL.md'), '# 上游改变了方法\n');
  f.git('add', f.sourcePath); f.git('commit', '-qm', 'change');
  assert.equal(checkUpstream(f).status, 'review_required');
  assert.equal(fs.readFileSync(f.lockFile, 'utf8'), before);
  assert.deepEqual(fs.readFileSync(f.chinesePath), chineseBefore);
});

test('CLI 的已审阅、待审阅和无效记录分别返回 0、2、1', t => {
  const f = fixture(t);
  const scriptDir = path.join(f.repo, 'scripts');
  fs.mkdirSync(scriptDir);
  const script = path.join(scriptDir, 'check-fuxi-wiki-upstream.mjs');
  fs.copyFileSync(new URL('./check-fuxi-wiki-upstream.mjs', import.meta.url), script);
  const lock = path.join(scriptDir, 'fuxi-wiki-upstream.json');
  fs.copyFileSync(f.lockFile, lock);
  const run = (...args) => spawnSync(process.execPath, [script, ...args], {encoding: 'utf8'});
  assert.equal(run().status, 0);
  fs.writeFileSync(path.join(f.repo, f.sourcePath, 'SKILL.md'), '# 新方法\n');
  f.git('add', f.sourcePath); f.git('commit', '-qm', 'change');
  assert.equal(run().status, 2);
  assert.equal(run('--ref', 'missing-ref').status, 1);
  for (const invalid of ['{', 'null', '{}', '{"sourcePath":"wrong","reviewedTrees":[]}']) {
    fs.writeFileSync(lock, invalid);
    const result = run();
    assert.equal(result.status, 1);
    assert.equal(JSON.parse(result.stderr).status, 'check_failed');
  }
});
test('新增支持文件也触发审阅，不只比较 SKILL.md', t => {
  const f = fixture(t);
  fs.writeFileSync(path.join(f.repo, f.sourcePath, 'reference.md'), '新约定');
  f.git('add', f.sourcePath); f.git('commit', '-qm', 'resource');
  assert.equal(checkUpstream(f).status, 'review_required');
});
test('无效引用和被删除的技能不能假装无变化', t => {
  const f = fixture(t);
  assert.throws(() => checkUpstream({...f, ref: 'missing-ref'}));
  f.git('rm', '-r', f.sourcePath); f.git('commit', '-qm', 'removed');
  assert.throws(() => checkUpstream(f));
});
