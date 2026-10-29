// 사용법: node card_templates/render.js <job.json> <outDir>
//   job.json = { template, brand, background, slides }  (README "작업 파일 형식" 참고)
//   경로(background, brand.mascots.*)는 job.json 위치 기준 상대경로 또는 절대경로.
// 슬라이드마다 1080×1350 PNG(01.png, 02.png …)를 outDir에 저장한다.
const fs = require('fs');
const path = require('path');
const { pathToFileURL } = require('url');

let chromium;
try {
  ({ chromium } = require('playwright'));
} catch (e) {
  ({ chromium } = require(path.join(require('os').homedir(), '.cardnews-deps', 'node_modules', 'playwright')));
}

const toUrl = (p, base) => (!p || /^(https?|file|data):/.test(p) ? p : pathToFileURL(path.resolve(base, p)).href);

(async () => {
  const [jobPath, outDir] = process.argv.slice(2);
  if (!jobPath || !outDir) { console.error('usage: node render.js job.json outDir'); process.exit(1); }
  const job = JSON.parse(fs.readFileSync(jobPath, 'utf8'));
  const base = path.dirname(path.resolve(jobPath));
  const templatePath = path.resolve(__dirname, job.template, 'template.html');
  if (!fs.existsSync(templatePath)) { console.error(`template not found: ${templatePath}`); process.exit(1); }

  job.background = toUrl(job.background, base);
  const mascots = (job.brand && job.brand.mascots) || {};
  for (const k of Object.keys(mascots)) mascots[k] = toUrl(mascots[k], base);

  fs.mkdirSync(outDir, { recursive: true });
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 1080, height: 1350 }, deviceScaleFactor: 1 });
    await page.goto(pathToFileURL(templatePath).href, { waitUntil: 'networkidle' });
    const n = await page.evaluate((j) => window.renderDeck(j), job);
    await page.evaluate(() => document.fonts.ready);
    await page.waitForFunction(() => [...document.images].every((img) => img.complete), null, { timeout: 15000 });
    await page.waitForTimeout(300);

    // 글자가 슬라이드 밖으로 넘치면 실패 처리(템플릿·문구 길이 조정 필요)
    const overflow = await page.evaluate(() => [...document.querySelectorAll('.slide')].flatMap((s) => {
      const box = s.getBoundingClientRect();
      return [...s.querySelectorAll('.content *, .foot *')].filter((el) => {
        const r = el.getBoundingClientRect();
        return r.width && (r.left < box.left - 1 || r.right > box.right + 1 || r.top < box.top - 1 || r.bottom > box.bottom + 1);
      }).map((el) => `slide ${s.dataset.i}: ${el.className || el.tagName} "${el.textContent.trim().slice(0, 20)}"`);
    }));
    if (overflow.length) { console.error('overflow:\n' + overflow.join('\n')); process.exitCode = 2; }

    const slides = await page.$$('.slide');
    for (let i = 0; i < slides.length; i++) {
      await slides[i].screenshot({ path: path.join(outDir, `${String(i + 1).padStart(2, '0')}.png`) });
    }
    console.log(`rendered ${n} slides with "${job.template}" -> ${outDir}`);
  } finally {
    await browser.close();
  }
})();
