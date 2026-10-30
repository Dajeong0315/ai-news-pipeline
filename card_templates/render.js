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
    await page.evaluate(() => window.fitDeck && window.fitDeck());
    await page.waitForFunction(() => [...document.images].every((img) => img.complete), null, { timeout: 15000 });
    await page.waitForTimeout(300);

    // 렌더 QA(2026-10-30 보완). errors는 실패 처리(문구를 다시 써서 재렌더), warnings는 로그만 남긴다.
    // - 본문(.content)·꼬리(.foot) 글자가 슬라이드 밖으로 넘침: 항상 실패(기존 기준)
    // - 장식(.deco) 속 글자(표지 말풍선·알림·폴더 목록 등)가 슬라이드 밖으로 넘침,
    //   overflow:hidden 칸 안에서 글자가 잘림: job.qa.strict면 실패, 아니면 경고
    //   (경제 템플릿은 장식이 일부러 밖으로 걸치는 경우가 있어 기본은 경고)
    // - 이미지가 깨짐(배경·마스코트 로드 실패): 항상 실패
    // - 웹폰트 로드 실패: 경고(대체 글꼴로 그려져 디자인이 달라짐)
    const strict = !!(job.qa && job.qa.strict);
    const qa = await page.evaluate((strict) => {
      const errors = [], warnings = [];
      const ownText = (el) => [...el.childNodes].some((c) => c.nodeType === 3 && c.textContent.trim());
      for (const s of document.querySelectorAll('.slide')) {
        const box = s.getBoundingClientRect();
        const outside = (r) => r.width && (r.left < box.left - 1 || r.right > box.right + 1 || r.top < box.top - 1 || r.bottom > box.bottom + 1);
        const tag = (el) => `slide ${s.dataset.i}: ${el.className || el.tagName} "${el.textContent.trim().slice(0, 20)}"`;
        for (const el of s.querySelectorAll('.content *, .foot *')) {
          if (outside(el.getBoundingClientRect())) errors.push('overflow ' + tag(el));
        }
        for (const el of s.querySelectorAll('.deco *')) {
          if (ownText(el) && outside(el.getBoundingClientRect())) (strict ? errors : warnings).push('deco overflow ' + tag(el));
        }
        for (const el of s.querySelectorAll('.content, .content *, .foot, .foot *, .deco *')) {
          const cs = getComputedStyle(el);
          if (!/hidden|clip/.test(cs.overflowX + ' ' + cs.overflowY) || !el.innerText || !el.innerText.trim()) continue;
          if (el.scrollWidth > el.clientWidth + 2 || el.scrollHeight > el.clientHeight + 2) (strict ? errors : warnings).push('clipped ' + tag(el));
        }
        for (const img of s.querySelectorAll('img')) {
          if (img.getAttribute('src') && !img.naturalWidth) errors.push(`slide ${s.dataset.i}: broken image ${String(img.getAttribute('src') || '').slice(-60)}`);
        }
      }
      document.fonts.forEach((f) => { if (f.status === 'error') warnings.push(`font failed: ${f.family}`); });
      return { errors, warnings: [...new Set(warnings)] };
    }, strict);
    for (const w of qa.warnings) console.log('warning: ' + w);
    if (qa.errors.length) { console.error('overflow:\n' + qa.errors.join('\n')); process.exitCode = 2; }

    const slides = await page.$$('.slide');
    for (let i = 0; i < slides.length; i++) {
      await slides[i].screenshot({ path: path.join(outDir, `${String(i + 1).padStart(2, '0')}.png`) });
    }
    console.log(`rendered ${n} slides with "${job.template}" -> ${outDir}`);
  } finally {
    await browser.close();
  }
})();
