import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';

const skill = fs.readFileSync(new URL('../skills/research/llm-wiki-fuxi/SKILL.md', import.meta.url), 'utf8');

test('source location and template state are reconciled by the maintained Chinese Skill', () => {
  assert.match(skill, /当前阅读器没有行号锚点合同/);
  assert.match(skill, /不能仅凭事实数为零推断为空/);
  assert.match(skill, /相关实体页、概念页、索引摘要及交叉链接说明/);
  const links = fs.readFileSync(new URL('../skills/research/llm-wiki-fuxi/references/link-correction.md', import.meta.url), 'utf8');
  assert.match(links, /证据行号不是来源包装页锚点/);
  assert.match(links, /\[\^requirement-a\]:.*\[\[raw\/material-a.md\|合成来源资料\]\].*B8:E8/);
  const fields = fs.readFileSync(new URL('../skills/research/llm-wiki-fuxi/references/field-fidelity.md', import.meta.url), 'utf8');
  assert.match(fields, /模板现状必须与关联页面一致/);
  assert.match(fields, /是否创建页面或如何调整关系仍由你判断/);
});
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

test('unchanged review is limited to the current transport slice including cells-only evidence', () => {
  assert.match(skill, /当前运输段/);
  assert.match(skill, /没有已接受事实.*reviewedFactIds.*空数组/);
  assert.match(skill, /不能把前一段.*事实标识/);
  assert.match(skill, /不会替你填写审阅决定/);
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

test('Chinese Skill owns field reconciliation and supplies a bounded synthetic example', () => {
  assert.match(skill, /references\/field-fidelity\.md/);
  const example = fs.readFileSync(new URL('../skills/research/llm-wiki-fuxi/references/field-fidelity.md', import.meta.url), 'utf8');
  for (const word of ['名称', '数值', '单位', '条件', '备注', '坐标']) assert.ok(example.includes(word), word);
  assert.match(example, /2 小时/);
  assert.match(example, /20 小时/);
  assert.match(example, /仅供参考/);
  assert.match(example, /记录数/);
  assert.match(example, /不.*猜/);
});

test('page template supplies required metadata without depending on a rewriting publisher', () => {
  const template = skill.split('## 页面约定与中文模板')[1]?.match(/```yaml\s*([\s\S]*?)```/)?.[1] || '';
  for (const field of ['title', 'created', 'updated', 'type', 'tags', 'sources', 'factRefs']) {
    assert.match(template, new RegExp(`^${field}:`, 'm'));
  }
  assert.match(skill, /发布器不会替你补元数据、改链接或补索引/);
});

test('clause example preserves every source obligation and its independent location', () => {
  const resource = fs.readFileSync(new URL('../skills/research/llm-wiki-fuxi/references/field-fidelity.md', import.meta.url), 'utf8');
  const section = resource.split('## 文本条款不等于字段清单')[1] || '';
  const sources = [...section.matchAll(/^\| ([A-Z]+\d+) \| ([^|]+) \|$/gm)]
    .map(([, address, text]) => ({address, text: text.trim()}));
  const example = section.match(/```markdown\s*([\s\S]*?)```/)?.[1] || '';
  assert.ok(sources.length > 0, 'The synthetic source must contain actual clauses');
  assert.ok(example, 'The resource must include a concrete business-writing example');
  for (const source of sources) {
    assert.ok(example.includes(source.text), `Missing source obligation: ${source.address}`);
    assert.ok(new RegExp(`工作表 ${source.address}。`).test(example), `Missing separate provenance: ${source.address}`);
  }
  assert.match(skill, /文本条款/);
  assert.match(section, /不是.*每个.*字段.*义务/);
  assert.match(section, /主题.*仍由你/);
  assert.match(section, /脚注.*不能代替/);
  assert.doesNotMatch(example, /详见原文|\.\.\.|…/);
});

test('source-cell references are not fabricated facts and corrections keep the source assertion', () => {
  assert.match(skill, /不能从 Sheet、行列坐标或分片序号拼出新标识/);
  assert.match(skill, /用对应可见来源页和精确 Sheet\/坐标保留出处/);
  assert.match(skill, /不能因此删除其他已核验引用或降低整库权威事实覆盖/);
  const resource = fs.readFileSync(new URL('../skills/research/llm-wiki-fuxi/references/field-fidelity.md', import.meta.url), 'utf8');
  const calls = [...resource.matchAll(/```json\s*([\s\S]*?)```/g)].map(match => JSON.parse(match[1]));
  assert.equal(calls.length, 2);
  assert.equal(calls[1].tool, 'patch');
  assert.equal(calls[0].path, calls[1].path);
  assert.equal(calls[1].new_string, calls[1].old_string.replaceAll('sheet-2::row-8-col-2', 'sheet-2:verified-record-1'));
  assert.match(resource, /没有任何已审阅事实标识涵盖/);
  assert.match(resource, /不能借用相邻记录/);
});
