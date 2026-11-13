import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test, after } from 'node:test';

// No browser, account files, network or send/edit operation is used.
const root = mkdtempSync(join(tmpdir(), 'ls-discord-test-'));
const savedHome = process.env.HOME;
const savedConfig = process.env.LS_DISCORD_CONFIG;
process.env.HOME = root;
process.env.LS_DISCORD_CONFIG = join(root, 'discord.json');
writeFileSync(process.env.LS_DISCORD_CONFIG, JSON.stringify({
  channel: 'https://discord.com/channels/111/222', name: 'fixture', server: 'fixture', handle: 'tester',
}));
const { check, postedMessage } = await import('./discord_tab.mjs');
after(() => {
  if (savedHome === undefined) delete process.env.HOME; else process.env.HOME = savedHome;
  if (savedConfig === undefined) delete process.env.LS_DISCORD_CONFIG; else process.env.LS_DISCORD_CONFIG = savedConfig;
  rmSync(root, { recursive: true });
});

test('another sender cannot prove our delivery', () => {
  const foreign = { id: '123', mine: false, text: 'Neutral update' };
  // The former predicate accepted this row.
  assert.equal([foreign].find(m => ![].includes(m.id) && /^\d+$/.test(m.id) && m.text.startsWith('Neutral')), foreign);
  assert.equal(postedMessage([foreign], [], 'Neutral'), undefined);
});

test('only a new own numeric message can be a delivery candidate', () => {
  const own = { id: '123', mine: true, text: 'Neutral update' };
  assert.equal(postedMessage([own], [], 'Neutral'), own);
  assert.equal(postedMessage([own], ['123'], 'Neutral'), undefined);
  assert.equal(postedMessage([{ ...own, id: 'pending' }], [], 'Neutral'), undefined);
  assert.equal(postedMessage([{ ...own, mine: undefined }], [], 'Neutral'), undefined);
  assert.equal(postedMessage([own], [], 'Different'), undefined);
});

test('draft validation refuses empty, private and overlong text', () => {
  for (const text of ['', '/home/someone/file', 'password=123456789', '@everyone update', 'x'.repeat(1901)]) {
    assert.notDeepEqual(check(text), []);
  }
  assert.deepEqual(check('Neutral update with no private data.'), []);
});

test('mentions need explicit permission; current team forms are not browser routine forms', () => {
  assert.notDeepEqual(check('<@123> Neutral'), []);
  assert.deepEqual(check('<@123> Neutral', { mentions: true }), []);
  assert.notDeepEqual(check('Neutral summary\n- Done: https://github.com/example/project/pull/7', { routine: true }), []);
});
