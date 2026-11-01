// Meta Business Suite 웹 화면에서 인스타 게시물을 "스토리에 다시 공유"한다(Graph API에 이 기능이 없음).
// share_to_story.py가 부른다. 사람이 직접 쓸 일은 --login(최초 1회 페이스북 로그인)뿐이다.
//   node fb_story_share.js <job.json>
//   job = { mode: "login"|"share", asset_id, media_id, shortcode, profile_dir, shot_dir, dry_run, headful }
// 결과는 stdout 마지막 줄 JSON: { ok, step, message, screenshot, copied_ids }
// 화면 경로(2026-11-01 Aside 수동 확인, 한국어 UI): 게시됨 목록 → 줄의 "⋯" → "스토리에 다시 공유" → 대화상자 "스토리에 추가".
// 게시물은 캡션이 아니라 "게시물 ID 복사"로 얻은 id(또는 줄 속 링크의 shortcode)를 media_id와 대조해 고른다.
const fs = require('fs');
const path = require('path');

let chromium;
try {
  ({ chromium } = require('playwright'));
} catch (e) {
  ({ chromium } = require(path.join(require('os').homedir(), '.cardnews-deps', 'node_modules', 'playwright')));
}

const MENU_SHARE = '스토리에 다시 공유';
const MENU_COPY_ID = '게시물 ID 복사';
const DIALOG_TITLE = 'Instagram 스토리에 다시 공유';
const ADD_BUTTON = '스토리에 추가';
const MAX_ROWS = 12;        // 최근 게시물 몇 줄까지 대조할지
const LIST_WAIT_MS = 120000; // 방금 올린 게시물이 목록에 나타날 때까지 기다리는 시간

const job = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const listUrl = `https://business.facebook.com/latest/posts/published_posts?asset_id=${job.asset_id}`;
const copied = [];

// 결과만 돌려주고, 브라우저를 닫은 뒤(쿠키가 프로필에 저장되게) 맨 끝에서 출력·종료한다
const done = (result) => result;

async function shot(page, name) {
  if (!page || !job.shot_dir) return null;
  fs.mkdirSync(job.shot_dir, { recursive: true });
  const file = path.join(job.shot_dir, `${new Date().toISOString().replace(/[:.]/g, '-')}-${name}.png`);
  await page.screenshot({ path: file, fullPage: false }).catch(() => {});
  return file;
}

// 로그인된 화면에도 숨은 로그인 입력칸이 있어(2026-11-01 첫 로그인 때 오판) 보이는 칸과 URL 경로만 본다
const loggedOut = async (page) =>
  /login|checkpoint|two_step|two_factor/.test(new URL(page.url()).pathname) ||
  (await page.locator('input[name="pass"]').filter({ visible: true }).count()) > 0;

async function closeIntroPopups(page) {
  for (const name of ['완료', '닫기', '나중에']) {
    const btn = page.getByRole('button', { name, exact: true });
    if (await btn.first().isVisible().catch(() => false)) await btn.first().click().catch(() => {});
  }
}

// 줄마다 "홍보하기" 옆의 "⋯"(접근성 이름 "드롭다운 열기", 2026-11-01 화면 확인). 위에서부터 최신 게시물 순.
const moreButtons = (page) => page.locator('[aria-haspopup="menu"]').filter({ hasText: '드롭다운 열기' });

async function openMenu(page, button) {
  await button.scrollIntoViewIfNeeded().catch(() => {});
  await button.click();
  await page.getByRole('menuitem').first().waitFor({ timeout: 5000 }).catch(() => {});
}

const menuItem = (page, name) => page.getByRole('menuitem', { name }).or(page.getByText(name, { exact: true })).first();

async function copiedIdFor(page, button) {
  await openMenu(page, button);
  const copy = menuItem(page, MENU_COPY_ID);
  if (!(await copy.isVisible().catch(() => false))) {
    await page.keyboard.press('Escape');
    return null;
  }
  await page.evaluate(() => { window.__cardnewsCopied = null; });
  await copy.click();
  await page.waitForTimeout(400);
  const value = await page.evaluate(() => window.__cardnewsCopied);
  await page.keyboard.press('Escape').catch(() => {});
  return value ? String(value).trim() : null;
}

async function run() {
  fs.mkdirSync(job.profile_dir, { recursive: true });
  const context = await chromium.launchPersistentContext(job.profile_dir, {
    headless: !(job.headful || job.mode === 'login'),
    viewport: { width: 1440, height: 1000 },
    locale: 'ko-KR',
  });
  // "게시물 ID 복사"가 클립보드에 쓰는 값을 가로챈다(헤드리스에선 클립보드 읽기 권한이 불안정)
  await context.addInitScript(() => {
    const keep = (v) => { window.__cardnewsCopied = v; };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      const orig = navigator.clipboard.writeText.bind(navigator.clipboard);
      navigator.clipboard.writeText = (t) => { keep(t); return orig(t).catch(() => {}); };
    }
    const exec = document.execCommand.bind(document);
    document.execCommand = (cmd, ...rest) => {
      if (String(cmd).toLowerCase() === 'copy') {
        const el = document.activeElement;
        keep((el && 'value' in el && el.value) || String(window.getSelection() || ''));
      }
      return exec(cmd, ...rest);
    };
    document.addEventListener('copy', (e) => {
      const t = e.clipboardData && e.clipboardData.getData('text/plain');
      if (t) keep(t);
    }, true);
  });
  const page = context.pages()[0] || (await context.newPage());
  try {
    await page.goto(listUrl, { waitUntil: 'domcontentloaded', timeout: 60000 });

    if (job.mode === 'login') {
      // 운영자가 열린 창에서 직접 로그인한다(비밀번호를 코드가 다루지 않는다). 게시물 목록이 보이면 끝.
      const deadline = Date.now() + 10 * 60000;
      while (Date.now() < deadline) {
        await page.waitForTimeout(3000);
        if (!(await loggedOut(page)) && page.url().includes('business.facebook.com')) {
          if (!page.url().includes('published_posts')) await page.goto(listUrl, { waitUntil: 'domcontentloaded' }).catch(() => {});
          await page.waitForTimeout(5000);
          if (!(await loggedOut(page))) return done({ ok: true, step: 'login', message: '로그인 확인', screenshot: await shot(page, 'login-ok') });
        }
      }
      return done({ ok: false, step: 'login', message: '10분 안에 로그인이 끝나지 않음', screenshot: await shot(page, 'login-timeout') });
    }

    await page.waitForTimeout(6000);
    if (await loggedOut(page)) {
      return done({ ok: false, step: 'login', message: '페이스북 로그인이 풀림(또는 2단계 인증 요청) — python share_to_story.py --login 으로 다시 로그인', screenshot: await shot(page, 'logged-out') });
    }
    await closeIntroPopups(page);

    // 방금 올린 게시물이 목록에 뜰 때까지 새로고침하며 찾는다
    const deadline = Date.now() + LIST_WAIT_MS;
    let target = null;
    while (!target && Date.now() < deadline) {
      await closeIntroPopups(page);
      // 줄 속 링크에 shortcode가 있으면 그 줄의 "⋯"만 확인
      const buttons = moreButtons(page);
      const n = Math.min(await buttons.count(), MAX_ROWS);
      for (let i = 0; i < n && !target; i++) {
        const button = buttons.nth(i);
        const id = await copiedIdFor(page, button).catch(() => null);
        if (id) copied.push(id);
        if (id && (id === String(job.media_id) || (job.shortcode && id.includes(job.shortcode)))) target = button;
      }
      if (!target) {
        if (Date.now() + 15000 > deadline) break;
        await page.waitForTimeout(15000);
        await page.reload({ waitUntil: 'domcontentloaded' }).catch(() => {});
        await page.waitForTimeout(6000);
      }
    }
    if (!target) {
      const labels = await page.locator('[role="button"], [aria-haspopup]').evaluateAll(
        (els) => els.slice(0, 80).map((e) => e.getAttribute('aria-label') || e.textContent.trim().slice(0, 20)).filter(Boolean));
      return done({ ok: false, step: 'find', message: `게시물 ID ${job.media_id}를 목록에서 못 찾음(화면 변경 가능). 버튼 라벨: ${labels.join(' | ')}`, screenshot: await shot(page, 'not-found') });
    }

    await openMenu(page, target);
    const share = menuItem(page, MENU_SHARE);
    if (!(await share.isVisible().catch(() => false))) {
      return done({ ok: false, step: 'menu', message: `메뉴에 "${MENU_SHARE}"가 없음`, screenshot: await shot(page, 'no-share-menu') });
    }
    await share.click();
    const dialog = page.getByRole('dialog').filter({ hasText: DIALOG_TITLE }).first();
    await dialog.waitFor({ timeout: 20000 }).catch(() => {});
    const add = dialog.getByRole('button', { name: ADD_BUTTON, exact: true });
    if (!(await add.isVisible().catch(() => false))) {
      return done({ ok: false, step: 'dialog', message: `대화상자 "${DIALOG_TITLE}" 또는 "${ADD_BUTTON}" 버튼이 안 보임`, screenshot: await shot(page, 'no-dialog') });
    }
    await page.waitForTimeout(2000); // 미리보기 그림이 다 그려진 뒤 찍는다
    if (job.dry_run) {
      const file = await shot(page, 'dry-run-dialog');
      await page.keyboard.press('Escape').catch(() => {});
      return done({ ok: true, step: 'dry_run', message: `"${ADD_BUTTON}" 직전에서 멈춤(누르지 않음)`, screenshot: file });
    }
    await add.click();
    await dialog.waitFor({ state: 'hidden', timeout: 60000 }).catch(() => {});
    return done({ ok: true, step: 'shared', message: `"${ADD_BUTTON}" 누름`, screenshot: await shot(page, 'shared') });
  } catch (e) {
    return done({ ok: false, step: 'error', message: String(e && e.message || e).slice(0, 500), screenshot: await shot(page, 'error') });
  } finally {
    await context.close().catch(() => {});
  }
}

run().catch((e) => ({ ok: false, step: 'launch', message: String(e && e.message || e).slice(0, 500) })).then((result) => {
  console.log(JSON.stringify({ copied_ids: copied, ...result }));
  process.exit(result.ok ? 0 : 1);
});
