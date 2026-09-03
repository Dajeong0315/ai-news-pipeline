// 사용법: node render.js <content.json 경로> <출력폴더>
const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');

let chromium;
try {
  ({ chromium } = require('playwright'));
} catch (e) {
  const depsPath = path.join(require('os').homedir(), '.cardnews-deps', 'node_modules', 'playwright');
  ({ chromium } = require(depsPath));
}

function fail(message) {
  console.error(message);
  process.exit(1);
}

(async () => {
  const contentPath = process.argv[2];
  const outDir = process.argv[3];
  if (!contentPath || !outDir) fail('usage: node render.js content.json outDir');

  const validatePath = path.resolve(__dirname, 'validate.js');
  const validation = spawnSync(process.execPath, [validatePath, contentPath], { stdio: 'inherit' });
  if (validation.status !== 0) fail('validation failed; render cancelled');

  const templatePath = path.resolve(__dirname, 'card-template-v2.html');
  if (!fs.existsSync(templatePath)) fail(`template not found: ${templatePath}`);

  const content = JSON.parse(fs.readFileSync(contentPath, 'utf8'));
  fs.mkdirSync(outDir, { recursive: true });

  // 재렌더 시 이전 카드가 남아 게시 순서를 오염시키지 않도록 번호 PNG만 제거한다.
  for (const name of fs.readdirSync(outDir)) {
    if (/^\d{2}\.png$/i.test(name)) fs.unlinkSync(path.join(outDir, name));
  }

  let browser;
  try {
    browser = await chromium.launch();
    const page = await browser.newPage({
      viewport: { width: 1080, height: 1400 },
      deviceScaleFactor: 1
    });
    await page.goto(`file:///${templatePath.replace(/\\/g, '/')}`, { waitUntil: 'networkidle' });
    await page.evaluate((data) => window.buildCards(data), content);
    await page.evaluate(() => document.fonts.ready);
    await page.waitForTimeout(400);

    const cards = await page.$$('.card');
    if (cards.length !== content.cards.length) {
      throw new Error(`DOM card count ${cards.length} != JSON card count ${content.cards.length}`);
    }

    const overflow = await page.$$eval('.card', (nodes) => nodes.flatMap((node, index) => {
      const cardBox = node.getBoundingClientRect();
      const textNodes = node.querySelectorAll(
        '.hook,.cover-sub,.stat-hero,.c-title,.c-body,.todo,.disc,.disc-note,.badge,.handle'
      );
      return Array.from(textNodes).filter((item) => {
        const box = item.getBoundingClientRect();
        const clippedByCard = box.left < cardBox.left - 1 || box.right > cardBox.right + 1
          || box.top < cardBox.top - 1 || box.bottom > cardBox.bottom + 1;
        // 브라우저의 한글 폰트 라운딩은 1~4px 차이를 만들 수 있어 8px 허용한다.
        const clippedInternally = item.scrollWidth > item.clientWidth + 8
          || item.scrollHeight > item.clientHeight + 8;
        return clippedByCard || clippedInternally;
      }).map((item) => ({ card: index + 1, className: item.className }));
    }));
    if (overflow.length) throw new Error(`card overflow detected: ${JSON.stringify(overflow)}`);

    for (let i = 0; i < cards.length; i += 1) {
      const n = String(i + 1).padStart(2, '0');
      await cards[i].screenshot({ path: path.join(outDir, `${n}.png`) });
    }
    console.log(`rendered ${cards.length} cards at 1080x1350 -> ${outDir}`);
  } finally {
    if (browser) await browser.close();
  }
})().catch((error) => fail(error.stack || error.message));
