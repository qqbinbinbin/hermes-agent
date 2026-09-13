import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';

const skill = fs.readFileSync(new URL('../skills/research/llm-wiki-fuxi/SKILL.md', import.meta.url), 'utf8');
function examples() {
  const section = skill.split('### 按实际结果选择回执')[1]?.split('## 持续维护')[0] || '';
  return [...section.matchAll(/```json\s*([\s\S]*?)```/g)].map(match => JSON.parse(match[1]));
}
test('changed-page receipt does not serialize the source fact collection again', () => {
  const [changed] = examples();
  assert.ok(changed, 'Chinese Skill must show the changed-page receipt');
  assert.deepEqual(Object.keys(changed).sort(), ['semanticPaths', 'turnNonce', 'unitId', 'version']);
  assert.ok(changed.semanticPaths.length > 0);
  assert.equal(changed.version, 'kb-wiki-unit-receipt-v1');
});
test('unchanged review retains explicit review evidence instead of pretending to edit', () => {
  const [, unchanged] = examples();
  assert.ok(unchanged, 'Chinese Skill must show the distinct unchanged-review receipt');
  assert.deepEqual(unchanged.semanticPaths, []);
  assert.equal(unchanged.reviewDisposition, 'no_semantic_change');
  assert.ok(unchanged.reviewReason);
  assert.ok(unchanged.reviewedFactIds.length > 0);
  assert.ok(unchanged.reviewedSources[0].path);
  assert.ok(unchanged.reviewedSources[0].sha256);
});
