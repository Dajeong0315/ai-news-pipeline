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
  // render.js와 같이 설치된 브라우저(runtime.local.json의 browser_channel, 예: msedge)를 쓴다.
  // 이 PC엔 Playwright 전용 Chromium이 없어 기본 launch()가 실패했다(2026-11-12 경제 수집 원문 0자).
  const browser = await chromium.launch(process.env.CARDNEWS_BROWSER_CHANNEL ? { channel: process.env.CARDNEWS_BROWSER_CHANNEL } : {});
  try {
    const page = await browser.newPage();
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 15000 });
    // 2026-11-14: 구글 리다이렉트가 1.5초 안에 끝나지 않아 11/12부터 경제 원문이 전부 0자였다.
    // 언론사 주소로 바뀔 때까지 최대 12초 기다린다(보통 2~5초).
    try {
      await page.waitForURL((u) => !u.toString().includes('news.google.com'), { timeout: 12000 });
    } catch (e) { /* 그대로 구글 주소를 출력 → 호출 측이 본문 건너뜀 */ }
    console.log(page.url());
  } catch (e) {
    console.error(e.message);
    process.exit(1);
  } finally {
    await browser.close();
  }
})();
