import assert from 'node:assert/strict';
import { test } from 'node:test';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
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
  git('add', '.'); git('commit', '-qm', 'fixture');
  const lockFile = path.join(repo, 'review.json');
  fs.writeFileSync(lockFile, JSON.stringify({sourcePath, reviewedTrees: [{commit: git('rev-parse', 'HEAD'), tree: git('rev-parse', `HEAD:${sourcePath}`), decision: '合成审阅'}]}));
  return {repo, lockFile, git, sourcePath};
}

test('版本变动但技能目录不变，无需重复审阅', t => {
  const f = fixture(t);
  fs.writeFileSync(path.join(f.repo, 'unrelated.txt'), '其他功能');
  f.git('add', 'unrelated.txt'); f.git('commit', '-qm', 'other');
  assert.equal(checkUpstream(f).status, 'reviewed');
});
test('正文变化要求审阅且不改写中文技能或记录', t => {
  const f = fixture(t); const before = fs.readFileSync(f.lockFile, 'utf8');
  fs.writeFileSync(path.join(f.repo, f.sourcePath, 'SKILL.md'), '# 上游改变了方法\n');
  f.git('add', f.sourcePath); f.git('commit', '-qm', 'change');
  assert.equal(checkUpstream(f).status, 'review_required');
  assert.equal(fs.readFileSync(f.lockFile, 'utf8'), before);
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
