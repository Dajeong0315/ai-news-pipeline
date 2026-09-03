// 사용법: node validate.js <content.json 경로>
const fs = require('fs');
const path = require('path');

const contentPath = process.argv[2];
if (!contentPath) {
  console.error('usage: node validate.js content.json');
  process.exit(1);
}

const errors = [];
let data;
try {
  data = JSON.parse(fs.readFileSync(contentPath, 'utf8'));
} catch (error) {
  console.error(`invalid JSON: ${error.message}`);
  process.exit(1);
}

const allowedSeasons = new Set(['spring', 'summer', 'jangma', 'autumn', 'winter']);
const allowedTypes = new Set(['cover', 'why', 'concept', 'meaning', 'action', 'disclaimer']);
const allowedVisuals = new Set(['question', 'hub', 'bars', 'timeline', 'doc-up', 'piggy-up', 'pair', 'flow', 'alert']);
const characterDir = path.resolve(__dirname, '..', 'character');

if (!allowedSeasons.has(data.season)) errors.push(`unsupported season: ${data.season}`);
if (!Array.isArray(data.cards)) errors.push('cards must be an array');

const cards = Array.isArray(data.cards) ? data.cards : [];
if (cards.length < 3 || cards.length > 6) errors.push(`card count must be 3-6; got ${cards.length}`);
if (cards[0]?.type !== 'cover') errors.push('first card must be cover');
if (cards.at(-1)?.type !== 'disclaimer') errors.push('last card must be disclaimer');

const visualCounts = new Map();
cards.forEach((card, index) => {
  const label = `card ${index + 1}`;
  if (!allowedTypes.has(card.type)) errors.push(`${label}: unsupported type ${card.type}`);
  if (card.title && String(card.title).replace(/<br\s*\/?>/gi, '').length > 36) {
    errors.push(`${label}: title exceeds 36 characters`);
  }
  if (card.body && String(card.body).replace(/<br\s*\/?>/gi, '').length > 90) {
    errors.push(`${label}: body exceeds 90 characters`);
  }
  if (card.type === 'cover' && card.hook && String(card.hook).replace(/<br\s*\/?>/gi, '').length > 20) {
    errors.push(`${label}: cover hook exceeds 20 characters`);
  }
  if (card.char && !fs.existsSync(path.join(characterDir, `${card.char}.png`))) {
    errors.push(`${label}: missing character asset ${card.char}.png`);
  }
  if (card.visual) {
    if (!allowedVisuals.has(card.visual.type)) errors.push(`${label}: unsupported visual ${card.visual.type}`);
    visualCounts.set(card.visual.type, (visualCounts.get(card.visual.type) || 0) + 1);
    if (card.visual.type === 'hub' && !['main', 'a', 'b'].every((key) => card.visual[key])) {
      errors.push(`${label}: hub requires main, a, b`);
    }
    if (card.visual.type === 'bars' && !Array.isArray(card.visual.items)) {
      errors.push(`${label}: bars requires items`);
    }
    if (card.visual.type === 'timeline' && !Array.isArray(card.visual.points)) {
      errors.push(`${label}: timeline requires points`);
    }
    if (card.visual.type === 'flow' && (!card.visual.a || !card.visual.b)) {
      errors.push(`${label}: flow requires a, b`);
    }
  }
});

for (const [type, count] of visualCounts) {
  if (count > 1) errors.push(`visual type "${type}" repeats ${count} times`);
}
if (visualCounts.get('bars') > 1) errors.push('bars may be used at most once');

const disclaimer = cards.at(-1);
if (disclaimer && !String(disclaimer.text || '').includes('교육·정보 제공용')) {
  errors.push('disclaimer must contain 교육·정보 제공용');
}

if (errors.length) {
  console.error(errors.map((item) => `ERROR: ${item}`).join('\n'));
  process.exit(1);
}
console.log(`validation passed: ${cards.length} cards`);
