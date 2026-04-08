#!/usr/bin/env node
/**
 * Generate default (site-wide) social preview image for D4Builder
 * Creates a 1200x630px PNG for use when no specific build is selected
 */

import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import puppeteer from 'puppeteer';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const OUTPUT_PATH = path.join(__dirname, '../public/social-previews/d4builder-preview.png');

const HTML = `
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body { width: 1200px; height: 630px; overflow: hidden; background: #0d0d0d; }
  </style>
</head>
<body>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 630" width="1200" height="630">
  <defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" style="stop-color:#1a0a0a;stop-opacity:1" />
      <stop offset="100%" style="stop-color:#0d0d0d;stop-opacity:1" />
    </linearGradient>
    <linearGradient id="accentGrad" x1="0%" y1="0%" x2="0%" y2="100%">
      <stop offset="0%" style="stop-color:#c41e00;stop-opacity:1" />
      <stop offset="100%" style="stop-color:#8B0000;stop-opacity:1" />
    </linearGradient>
  </defs>

  <!-- Background -->
  <rect width="1200" height="630" fill="url(#bg)" />

  <!-- Left accent bar -->
  <rect width="6" height="630" fill="url(#accentGrad)" />

  <!-- Decorative diamond dividers -->
  <g transform="translate(60, 180)" fill="#8B0000" opacity="0.6">
    <polygon points="0,8 8,0 16,8 8,16" />
    <line x1="20" y1="8" x2="1060" y2="8" stroke="#8B0000" stroke-width="1" opacity="0.3"/>
    <polygon points="1064,8 1072,0 1080,8 1072,16" />
  </g>
  <g transform="translate(60, 430)" fill="#8B0000" opacity="0.6">
    <polygon points="0,8 8,0 16,8 8,16" />
    <line x1="20" y1="8" x2="1060" y2="8" stroke="#8B0000" stroke-width="1" opacity="0.3"/>
    <polygon points="1064,8 1072,0 1080,8 1072,16" />
  </g>

  <!-- Main title -->
  <text x="600" y="280" font-family="Cinzel, Georgia, serif" font-size="96" font-weight="700"
        fill="#ffffff" text-anchor="middle" letter-spacing="4">
    D4 Builder
  </text>

  <!-- Subtitle -->
  <text x="600" y="355" font-family="Cinzel, Georgia, serif" font-size="28" font-weight="400"
        fill="#c41e00" text-anchor="middle" letter-spacing="8">
    SEASON 12 · SEASON OF SLAUGHTER
  </text>

  <!-- Description -->
  <text x="600" y="410" font-family="Georgia, serif" font-size="20"
        fill="#999999" text-anchor="middle">
    Top-tier Diablo IV build guides · All classes · Skills, gear &amp; stat priorities
  </text>

  <!-- Class list -->
  <text x="600" y="500" font-family="Roboto Condensed, sans-serif" font-size="16"
        fill="#666666" text-anchor="middle" letter-spacing="3">
    BARBARIAN · DRUID · NECROMANCER · PALADIN · ROGUE · SORCERER · SPIRITBORN
  </text>

  <!-- Bottom branding -->
  <text x="600" y="590" font-family="Cinzel, serif" font-size="14"
        fill="#444444" text-anchor="middle">
    d4builder.com
  </text>
</svg>
</body>
</html>
`.trim();

async function generate() {
  fs.mkdirSync(path.dirname(OUTPUT_PATH), { recursive: true });

  const browser = await puppeteer.launch({ headless: 'new' });
  const page = await browser.newPage();
  await page.setViewport({ width: 1200, height: 630 });
  await page.setContent(HTML);
  await page.screenshot({ path: OUTPUT_PATH, type: 'png', omitBackground: false });
  await browser.close();

  console.log(`✅ Default preview generated: ${OUTPUT_PATH}`);
}

generate().catch((err) => {
  console.error('❌ Failed:', err.message);
  process.exit(1);
});
