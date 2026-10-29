// 카드뉴스 템플릿 공용 엔진 (브라우저에서 실행).
// 템플릿 HTML이 window.TEMPLATE을 정의하고 이 파일을 불러오면, render.js가
// window.renderDeck(job)을 호출해 슬라이드를 그린다. 슬라이드 종류별 의미 구조
// (kicker/title/value 등 클래스)는 여기서 통일하고, 모양은 템플릿 CSS가 정한다.
//
// job = {
//   brand: { name, handle, wordmark, series, mascots: { default, cover, stat, ... } },
//   background: "file:///.../bg.jpg",
//   slides: [ { kind: "cover" | "stat" | "compare" | "point" | "list" | "quote" | "closing", ... } ]
// }

(function () {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  // 줄바꿈(\n)은 그대로 살리고, 나머지는 브라우저 줄바꿈(keep-all)에 맡긴다.
  const text = (s) => esc(s).replace(/\n/g, "<br>");
  const opt = (cls, s, tag = "p") => (s ? `<${tag} class="${cls}">${text(s)}</${tag}>` : "");

  // 손그림 장식(무드·스티커 템플릿 공용). currentColor로 색을 받는다.
  const DOODLES = {
    arrowCurve: `<svg viewBox="0 0 160 120" fill="none" stroke="currentColor" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20c40-12 92 4 104 40 6 20-6 38-22 34-14-4-8-26 10-26 16 0 30 18 40 40"/><path d="M128 96l16 14 2-22"/></svg>`,
    arrowDown: `<svg viewBox="0 0 80 140" fill="none" stroke="currentColor" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"><path d="M40 8c-14 30 16 50 0 80-4 10-2 22 0 40"/><path d="M24 112l16 18 16-18"/></svg>`,
    sparkle: `<svg viewBox="0 0 60 60" fill="currentColor"><path d="M30 0c2 16 8 26 30 30-22 4-28 14-30 30-2-16-8-26-30-30 22-4 28-14 30-30z"/></svg>`,
    sparkleLine: `<svg viewBox="0 0 60 60" fill="none" stroke="currentColor" stroke-width="3.5" stroke-linejoin="round"><path d="M30 4c2 14 8 22 26 26-18 4-24 12-26 26-2-14-8-22-26-26 18-4 24-12 26-26z"/></svg>`,
    swirl: `<svg viewBox="0 0 120 80" fill="none" stroke="currentColor" stroke-width="4.5" stroke-linecap="round"><path d="M6 60c20-40 60-50 70-26 8 18-14 30-22 16-8-14 16-34 40-30 10 2 18 8 20 14"/></svg>`,
    bars: `<svg viewBox="0 0 64 56" fill="none" stroke="currentColor" stroke-width="4.5" stroke-linecap="round"><path d="M8 50h48M16 42V30M30 42V18M44 42V6"/></svg>`,
    ribbon: `<svg viewBox="0 0 80 60" fill="currentColor"><path d="M40 26c-10-18-34-24-34-8 0 12 22 12 34 8zm0 0c10-18 34-24 34-8 0 12-22 12-34 8zM36 26l-14 30 12-4 6 8 0-34zm8 0l14 30-12-4-6 8 0-34z"/></svg>`,
    flower: `<svg viewBox="0 0 64 64" fill="currentColor"><circle cx="32" cy="14" r="12"/><circle cx="50" cy="28" r="12"/><circle cx="43" cy="49" r="12"/><circle cx="21" cy="49" r="12"/><circle cx="14" cy="28" r="12"/><circle cx="32" cy="33" r="9" fill="#F6C945"/></svg>`,
  };

  function content(slide) {
    switch (slide.kind) {
      case "cover":
        return opt("kicker", slide.kicker) + `<h1 class="title">${text(slide.title)}</h1>` + opt("subtitle", slide.subtitle);
      case "stat":
        return opt("kicker", slide.kicker) + `<p class="value">${T.value ? T.value(slide.value) : text(slide.value)}</p>` + opt("label", slide.label) + opt("note", slide.note);
      case "compare":
        return opt("kicker", slide.kicker) + opt("title", slide.title, "h2") +
          `<ul class="compare">${(slide.items || []).map((it) =>
            `<li><span class="c-label">${text(it.label)}</span><span class="c-value">${T.value ? T.value(it.value, true) : text(it.value)}</span></li>`).join("")}</ul>` +
          opt("note", slide.note);
      case "point":
        return opt("kicker", slide.kicker) + opt("title", slide.title, "h2") + opt("body", slide.body);
      case "list":
        return opt("kicker", slide.kicker) + opt("title", slide.title, "h2") +
          `<ol class="items">${(slide.items || []).map((it, i) => `<li><span class="n">${i + 1}</span><span>${text(it)}</span></li>`).join("")}</ol>`;
      case "quote":
        return `<blockquote class="quote">${text(slide.quote)}</blockquote>` + opt("source", slide.source);
      case "closing":
        return opt("kicker", slide.kicker) + opt("title", slide.title, "h2") + opt("body", slide.body);
      default:
        return opt("title", slide.title, "h2") + opt("body", slide.body);
    }
  }

  let T = {};
  window.DOODLES = DOODLES;
  window.esc = esc;

  window.renderDeck = function (job) {
    T = window.TEMPLATE || {};
    const n = job.slides.length;
    const brand = job.brand || {};
    const mascot = (slide, i) => {
      const m = brand.mascots || {};
      return m[`${slide.kind}-${i}`] || m[slide.kind] || m.default || "";
    };
    const html = job.slides.map((slide, idx) => {
      const i = idx + 1;
      const ctx = { i, n, brand, job, mascot: mascot(slide, i), doodles: DOODLES, esc, text, last: i === n };
      const variant = T.variant ? T.variant(slide, ctx) : "";
      const deco = T.deco ? T.deco(slide, ctx) : "";
      const inner = T.content ? T.content(slide, ctx, content) : content(slide);
      const foot = T.foot ? T.foot(slide, ctx)
        : `<footer class="foot"><span class="handle">${esc(brand.handle || "")}</span><span class="page">${i}/${n}</span></footer>`;
      return `<section class="slide k-${slide.kind} ${variant}" data-i="${i}">
        <div class="bg"><img src="${esc(job.background)}" alt=""></div><div class="shade"></div>
        <div class="deco">${deco}</div>
        <div class="content">${inner}</div>${foot}</section>`;
    }).join("");
    document.getElementById("deck").innerHTML = html;
    return n;
  };
})();
