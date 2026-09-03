// 사용법: node resolve_url.js <google-news-url>
// Google News RSS 링크는 JS 기반 리다이렉트라 requests로는 실제 기사 URL을
// 못 얻는다. 헤드리스 브라우저로 리다이렉트가 끝난 뒤 최종 URL을 출력한다.
let chromium;
try {
  ({ chromium } = require('playwright'));
} catch (e) {
  const path = require('path');
  const depsPath = path.join(require('os').homedir(), '.cardnews-deps', 'node_modules', 'playwright');
  ({ chromium } = require(depsPath));
}

(async () => {
  const url = process.argv[2];
  if (!url) { console.error('usage: node resolve_url.js <url>'); process.exit(1); }
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 15000 });
    await page.waitForTimeout(1500);
    console.log(page.url());
  } catch (e) {
    console.error(e.message);
    process.exit(1);
  } finally {
    await browser.close();
  }
})();
