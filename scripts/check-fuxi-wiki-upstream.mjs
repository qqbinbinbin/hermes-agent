import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

export function checkUpstream({ repo = root, ref = 'HEAD', lockFile = path.join(root, 'scripts/fuxi-wiki-upstream.json') } = {}) {
  const lock = JSON.parse(fs.readFileSync(lockFile, 'utf8'));
  if (lock.sourcePath !== 'skills/research/llm-wiki' || !Array.isArray(lock.reviewedTrees) || !lock.reviewedTrees.length ||
      lock.reviewedTrees.some(row => !/^[a-f0-9]{40}$/.test(row.commit) || !/^[a-f0-9]{40}$/.test(row.tree) || !row.decision?.trim())) {
    throw new Error('上游审阅记录无效');
  }
  const git = (...args) => execFileSync('git', ['-C', repo, ...args], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim();
  const commit = git('rev-parse', '--verify', '--end-of-options', `${ref}^{commit}`);
  const tree = git('rev-parse', '--verify', `${commit}:${lock.sourcePath}`);
  if (git('cat-file', '-t', tree) !== 'tree') throw new Error('上游技能目录无效');
  return { status: lock.reviewedTrees.some(row => row.tree === tree) ? 'reviewed' : 'review_required', commit, tree,
    sourcePath: lock.sourcePath, message: lock.reviewedTrees.some(row => row.tree === tree)
      ? '上游技能与已审阅基线一致，不需要重复合并。'
      : '上游技能存在未审阅变化；请分析差异并决定合并、改写或不采纳，不得自动覆盖中文技能。' };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const args = process.argv.slice(2);
    if (args.length && (args.length !== 2 || args[0] !== '--ref')) throw new Error('用法：node scripts/check-fuxi-wiki-upstream.mjs [--ref 提交或分支]');
    const result = checkUpstream({ ref: args[1] ?? 'HEAD' });
    console.log(JSON.stringify(result, null, 2));
    process.exitCode = result.status === 'reviewed' ? 0 : 2;
  } catch (error) {
    console.error(JSON.stringify({ status: 'check_failed', message: '无法核验上游技能；检查引用、Git 对象和审阅记录，不得当作无变化。' }));
    process.exitCode = 1;
  }
}
