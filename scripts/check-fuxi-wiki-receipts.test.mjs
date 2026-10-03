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
test('Chinese Skill owns the review reason and corrects receipt formatting in the same turn', () => {
  assert.match(skill, /先在 `log\.md` 追加本次审阅的具体中文业务理由/);
  assert.match(skill, /同一句理由逐字填入内部回执的 `reviewReason`/);
  assert.match(skill, /同一回合自行核对并修正后重交/);
  assert.match(skill, /平台不会代写你的业务判断/);
});

test('Chinese Skill distinguishes evidence reads from published source declarations', () => {
  assert.match(skill, /`RAW` 是取证输入/);
  assert.match(skill, /`PUBLISHED_SOURCE` 是实际可见的来源包装页/);
  assert.match(skill, /保留实际 Sheet、单元格坐标和内部 factRefs/);
  assert.match(skill, /不从允许列表任意选一项/);
  assert.match(skill, /列表标为截断时回查完整阅读目录/);
  const examples = fs.readFileSync(new URL('../skills/research/llm-wiki-fuxi/references/chinese-examples.md', import.meta.url), 'utf8');
  assert.match(examples, /sources: \[raw\/source.md\]/);
  assert.match(examples, /不能抄入 `sources`/);
  assert.match(examples, /不决定是否创建主题或如何组织知识/);
});
