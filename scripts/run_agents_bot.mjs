// Runs the real DK Analytics Terminal in a headless browser, runs the agent team
// on every game that hasn't tipped off, and writes picks.json for the site.
// Needs the GEMINI_API_KEY secret. Used by .github/workflows/agent-picks.yml.
import { chromium } from 'playwright';
import fs from 'fs';
import path from 'path';

const key = process.env.GEMINI_API_KEY;
const picksPerGame = Number(process.env.PICKS_PER_GAME || 3);

const browser = await chromium.launch();
const ctx = await browser.newContext({ timezoneId: 'America/Phoenix' });
const page = await ctx.newPage();
page.on('pageerror', e => console.log('page error:', e.message.slice(0, 200)));

await page.goto('file://' + path.resolve('index.html') + '?bot=1');
await page.waitForFunction(() => typeof S !== 'undefined' && !S.loadingSlate && S.games.length >= 0 && S.season, null, { timeout: 120000 });
await page.waitForTimeout(3000);
const slate = await page.evaluate(() => ({ date: S.date, games: S.games.filter(g => !g.status.state || g.status.state === 'pre').map(g => ({ id: g.id, short: g.short })) }));
const props = await page.evaluate(() => Object.keys(S.props).length);
console.log(`Slate ${slate.date}: ${slate.games.length} upcoming games, ${props} players with DraftKings props (ESPN reachable)`);
if (!key) { console.error('GEMINI_API_KEY secret is missing. Add it under Settings > Secrets and variables > Actions.'); await browser.close(); process.exit(1); }

// existing picks for today: keep games that have since tipped off
let out = { date: slate.date, games: {} };
try { const old = JSON.parse(fs.readFileSync('picks.json', 'utf8')); if (old.date === slate.date) out.games = old.games || {}; } catch (e) {}

if (slate.games.length) {
  await page.evaluate(([k, n]) => { AG.key = k; AG.nPicks = n; AG.model = ''; }, [key, picksPerGame]);
  await page.waitForFunction(() => BOX.status === 'ready', null, { timeout: 300000 }).catch(() => console.log('box scores still loading, continuing'));
  for (const g of slate.games) {
    const r = await page.evaluate(async gid => {
      S.gameFilter = gid; AG.error = null;
      await runAgents();
      return { res: AG.results[gid] || null, err: AG.error };
    }, g.id);
    console.log(`${g.short}: ${r.err ? 'FAILED - ' + r.err : r.res.picks.length + ' picks (' + r.res.model + ')'}`);
    if (r.res) out.games[g.id] = r.res;
    await page.waitForTimeout(8000); // stay well under Gemini's free per-minute limit
  }
}
out.updated = new Date().toISOString();
fs.writeFileSync('picks.json', JSON.stringify(out));
console.log(`Wrote picks.json with ${Object.keys(out.games).length} games`);
await browser.close();
