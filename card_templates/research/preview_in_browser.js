// 브라우저(Aside REPL)에서 템플릿을 렌더하고 render.js와 같은 QA를 돌리는 도구 (2026-10-30)
// 사용: 이 파일 내용을 REPL에서 읽어 eval한 뒤 await previewTemplate({...}) 호출.
// 파이썬·노드를 못 돌리는 세션에서도 미리보기와 렌더 QA를 할 수 있다.
async function previewTemplate({ root, template, brandFile, deckFile, background, outPrefix, mascot = false, strict = true }) {
  const toUrl = (p) => 'file:///' + p.replace(/\\/g, '/').split('/').map((s, i) => (i === 0 ? s : encodeURIComponent(s))).join('/');
  const ct = path.join(root, 'card_templates');
  const brand = JSON.parse(await fs.readFile(path.join(ct, 'brands', brandFile), 'utf8'));
  brand.mascots = Object.fromEntries(Object.entries(brand.mascots || {}).map(([k, v]) => [k, toUrl(path.resolve(path.join(ct, 'brands'), v))]));
  const deck = JSON.parse(await fs.readFile(deckFile, 'utf8'));
  await openTab('about:blank');
  await page.setViewportSize({ width: 1080, height: 1350 });
  await page.goto(toUrl(path.join(ct, template, 'template.html')), { waitUntil: 'networkidle' });
  await page.evaluate(() => { const st = document.createElement('style'); st.textContent = '::-webkit-scrollbar{display:none} html{scrollbar-width:none}'; document.head.appendChild(st); });
  const job = { template, mascot, brand, background: toUrl(background), slides: deck.slides, props: deck.props || {} };
  const n = await page.evaluate((j) => window.renderDeck(j), job);
  await page.evaluate(() => document.fonts.ready);
  await page.evaluate(() => window.fitDeck && window.fitDeck());
  await sleep(500);
  const qa = await page.evaluate((strict) => {
    const errors = [], warnings = [];
    const ownText = (el) => [...el.childNodes].some((c) => c.nodeType === 3 && c.textContent.trim());
    for (const s of document.querySelectorAll('.slide')) {
      const box = s.getBoundingClientRect();
      const outside = (r) => r.width && (r.left < box.left - 1 || r.right > box.right + 1 || r.top < box.top - 1 || r.bottom > box.bottom + 1);
      const tag = (el) => 'slide ' + s.dataset.i + ': ' + (el.className || el.tagName) + ' "' + el.textContent.trim().slice(0, 20) + '"';
      for (const el of s.querySelectorAll('.content *, .foot *')) if (outside(el.getBoundingClientRect())) errors.push('overflow ' + tag(el));
      for (const el of s.querySelectorAll('.deco *')) if (ownText(el) && outside(el.getBoundingClientRect())) (strict ? errors : warnings).push('deco overflow ' + tag(el));
      for (const el of s.querySelectorAll('.content, .content *, .foot, .foot *, .deco *')) {
        const cs = getComputedStyle(el);
        if (!/hidden|clip/.test(cs.overflowX + ' ' + cs.overflowY) || !el.innerText || !el.innerText.trim()) continue;
        if (el.scrollWidth > el.clientWidth + 2 || el.scrollHeight > el.clientHeight + 2) (strict ? errors : warnings).push('clipped ' + tag(el));
      }
      for (const img of s.querySelectorAll('img')) if (img.getAttribute('src') && !img.naturalWidth) errors.push('slide ' + s.dataset.i + ': broken image');
    }
    document.fonts.forEach((f) => { if (f.status === 'error') warnings.push('font failed: ' + f.family); });
    return { errors, warnings: [...new Set(warnings)] };
  }, strict);
  const shots = [];
  const count = await page.locator('.slide').count();
  for (let i = 0; i < count; i++) {
    const p = outPrefix + '-' + String(i + 1).padStart(2, '0') + '.jpg';
    const box = await page.evaluate((k) => { const el = document.querySelectorAll('.slide')[k]; el.scrollIntoView(); const r = el.getBoundingClientRect(); return { x: r.left, y: r.top, width: r.width, height: r.height }; }, i);
    await page.screenshot({ path: p, type: 'jpeg', quality: 85, clip: box });
    shots.push(p);
  }
  await closeTab(page);
  return { n, ...qa, shots };
}
