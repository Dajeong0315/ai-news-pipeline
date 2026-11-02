// 카드뉴스 템플릿 공용 엔진 (브라우저에서 실행).
// 템플릿 HTML이 window.TEMPLATE을 정의하고 이 파일을 불러오면, render.js가
// window.renderDeck(job)을 호출해 슬라이드를 그린다. 슬라이드 종류별 의미 구조
// (kicker/title/value 등 클래스)는 여기서 통일하고, 모양은 템플릿 CSS가 정한다.
//
// job = {
//   brand: { name, handle, wordmark, series, mascots: { default, cover, stat, ... } },
//   background: "file:///.../bg.jpg",
//   slides: [ { kind: "cover" | "stat" | "compare" | "point" | "list" | "quote" | "closing", ... } ],
//   mascot: false   // 선택. false면 마스코트 없는 버전(슬라이드에 .no-mascot 클래스)
// }
//
// 제목·인용·큰 숫자는 슬라이드 데이터의 줄바꿈(\n)대로만 줄을 나누고, 한 줄이 칸을
// 넘으면 글자 크기를 줄여 맞춘다(window.fitDeck). 의미 단위 줄바꿈을 지켜 가독성을 보장.

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

  // --- 강조·장별 배치(2026-11-02 카드 구조 v2) ---
  // 강조: 문구 속 **단어**를 채널 강조색 형광펜으로 칠한다. 템플릿마다 글자를 넣는 함수가 달라(말풍선·제목 쪼개기 등)
  // 데이터 단계에서 보이지 않는 표시 문자(U+E000/E001)로 바꿔 두고, 덱 HTML이 다 만들어진 뒤 <mark>로 바꾼다.
  const HL_OPEN = "", HL_CLOSE = "";
  const markEmphasis = (v) => typeof v === "string" ? v.replace(/\*\*(.+?)\*\*/g, `${HL_OPEN}$1${HL_CLOSE}`)
    : Array.isArray(v) ? v.map(markEmphasis)
    : v && typeof v === "object" ? Object.fromEntries(Object.entries(v).map(([k, x]) => [k, k === "layout" ? x : markEmphasis(x)])) : v;
  // 배치: slide.layout = {tone: 밝음|어두움|강조색, title_pos: 상단|중앙|하단, margin: 넓게|보통, icon: 이름}.
  // 템플릿마다 받을 수 있는 값만 반영하고, 범위를 벗어난 값은 무시하고 LAYOUT_LOG에 남긴다(render.js가 경고로 찍음).
  const TONE = { "밝음": "light", "어두움": "dark", "강조색": "accent" };
  const POS = { "상단": "top", "중앙": "center", "하단": "bottom" };
  const MARGIN = { "넓게": "wide", "보통": "normal" };
  const PHOTO_BG = ["gallery", "moody", "sticker", "darkcenter", "blocktitle", "cutout", "magazine", "edit", "airdrop"];
  // 11/2 렌더 확인: 큰 마스코트가 아래에 있는 moody·sticker·cutout은 위치를 옮기면 글자와 겹친다
  const SIMPLE_POS = ["gallery", "darkcenter", "blocktitle", "magazine"];
  // 오른쪽 위에 자체 장식(리본·컷아웃·사진 액자·메뉴 막대)이 있는 템플릿은 배치 아이콘을 넣지 않는다
  const ICON_OK = ["gallery", "moody", "darkcenter", "blocktitle", "magazine", "chat", "soft", "folder", "airdrop"];
  const ICON_PATHS = {
    "돈": '<circle cx="12" cy="12" r="9"/><path d="M8 8l2 8 2-6 2 6 2-8M7 12h10"/>',
    "달력": '<rect x="3.5" y="5" width="17" height="15.5" rx="2"/><path d="M3.5 10h17M8 3v4M16 3v4"/>',
    "체크": '<circle cx="12" cy="12" r="9"/><path d="M7.5 12.5l3 3 6-6.5"/>',
    "경고": '<path d="M12 3.5l9.5 16.5h-19z"/><path d="M12 10v4.5M12 17.2v.3"/>',
    "화살표": '<path d="M4 12h15M13 6l6 6-6 6"/>',
    "집": '<path d="M3.5 11L12 4l8.5 7"/><path d="M6 9.5V20h12V9.5M10 20v-5h4v5"/>',
    "카드": '<rect x="2.5" y="5.5" width="19" height="13" rx="2"/><path d="M2.5 10h19M6 15h4"/>',
    "서류": '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4M9 12h6M9 16h6"/>',
    "사람": '<circle cx="12" cy="8" r="4"/><path d="M4.5 20.5c1-4 4-6 7.5-6s6.5 2 7.5 6"/>',
    "비교 저울": '<path d="M12 4v16M7 20h10M5 7h14"/><path d="M5 7l-2.5 6h5zM19 7l-2.5 6h5z"/>',
  };
  window.LAYOUT_ICONS = Object.keys(ICON_PATHS);
  window.LAYOUT_LOG = [];
  function layoutOf(slide, i, template) {
    const lay = slide.layout || {}, cls = [], log = (m) => window.LAYOUT_LOG.push(`layout ${i}장 ${m}`);
    if (lay.tone) {
      if (!TONE[lay.tone]) log(`tone ${JSON.stringify(lay.tone)} 모름`);
      else if (!PHOTO_BG.includes(template)) log(`tone ${lay.tone}: ${template}은 사진 배경이 없어 무시`);
      else cls.push(`tone-${TONE[lay.tone]}`);
    }
    if (lay.title_pos) {
      if (!POS[lay.title_pos]) log(`title_pos ${JSON.stringify(lay.title_pos)} 모름`);
      else if (!SIMPLE_POS.includes(template) || slide.kind === "cover") log(`title_pos ${lay.title_pos}: ${template}${slide.kind === "cover" ? " 표지" : ""}는 위치 고정이라 무시`);
      else cls.push(`pos-${POS[lay.title_pos]}`);
    }
    if (lay.margin) {
      if (!MARGIN[lay.margin]) log(`margin ${JSON.stringify(lay.margin)} 모름`);
      else cls.push(`margin-${MARGIN[lay.margin]}`);
    }
    let icon = "";
    if (lay.icon) {
      if (!ICON_PATHS[lay.icon]) log(`icon ${JSON.stringify(lay.icon)} 목록에 없음`);
      else if (!ICON_OK.includes(template)) log(`icon ${lay.icon}: ${template}은 오른쪽 위 장식과 겹쳐 무시`);
      else icon = `<span class="lay-icon" data-overlap-ok><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${ICON_PATHS[lay.icon]}</svg></span>`;
    }
    return { cls: cls.join(" "), icon };
  }

  let T = {};
  window.DOODLES = DOODLES;
  window.esc = esc;

  window.renderDeck = function (job) {
    T = window.TEMPLATE || {};
    job = { ...job, slides: job.slides.map(markEmphasis) };
    window.LAYOUT_LOG = [];
    const n = job.slides.length;
    const brand = job.brand || {};
    if (job.mascot === false) brand.mascots = {};
    const mascot = (slide, i) => {
      const m = brand.mascots || {};
      return m[`${slide.kind}-${i}`] || m[slide.kind] || m.default || "";
    };
    const html = job.slides.map((slide, idx) => {
      const i = idx + 1;
      // props: 덱마다 내용에 맞춰 새로 짓는 장식 문구(창 제목·버튼·필기체 등). 없으면 템플릿 기본값.
      const ctx = { i, n, brand, job, props: job.props || {}, mascot: mascot(slide, i), doodles: DOODLES, esc, text, last: i === n };
      const variant = T.variant ? T.variant(slide, ctx) : "";
      const deco = T.deco ? T.deco(slide, ctx) : "";
      const inner = T.content ? T.content(slide, ctx, content) : content(slide);
      const foot = T.foot ? T.foot(slide, ctx)
        : `<footer class="foot"><span class="handle">${esc(brand.handle || "")}</span><span class="page">${i}/${n}</span></footer>`;
      const lay = layoutOf(slide, i, job.template);
      return `<section class="slide k-${slide.kind} ${variant} ${ctx.mascot ? "has-mascot" : "no-mascot"} ${lay.cls}" data-i="${i}">
        <div class="bg"><img src="${esc(job.background)}" alt=""></div><div class="shade"></div><div class="tone"></div>
        <div class="deco">${deco}${lay.icon}</div>
        <div class="content">${inner}</div>${foot}</section>`;
    }).join("");
    const deckEl = document.getElementById("deck");
    deckEl.innerHTML = html.replace(//g, '<mark class="hl">').replace(//g, "</mark>");
    if (brand.accent) deckEl.style.setProperty("--hl", brand.accent);
    return n;
  };

  // 줄바꿈은 데이터대로만, 넘치면 글자를 줄인다(최소 min px). 폰트 로딩 뒤에 호출.
  window.fitDeck = function () {
    const shrink = (el, min) => {
      el.style.whiteSpace = "nowrap";
      let size = parseFloat(getComputedStyle(el).fontSize);
      const limit = () => el.parentElement.getBoundingClientRect().width;
      while (el.scrollWidth > Math.ceil(limit()) + 1 && size > min) {
        size -= 2;
        el.style.fontSize = size + "px";
      }
      if (el.scrollWidth > Math.ceil(limit()) + 1) el.style.whiteSpace = "normal"; // 최소 크기로도 안 되면 자동 줄바꿈
    };
    document.querySelectorAll(".slide .title").forEach((el) => shrink(el, 48));
    document.querySelectorAll(".slide .quote").forEach((el) => shrink(el, 44));
    document.querySelectorAll(".slide .value:not(:has(.tiles))").forEach((el) => shrink(el, 90));
  };
})();
