#!/usr/bin/env node
/**
 * Generate social preview images for all builds
 * Creates 1200x630px PNG files for social media sharing using Puppeteer
 */

import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import puppeteer from 'puppeteer';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const WEBAPP_ROOT = path.join(__dirname, '..');
const PUBLIC_DIR = path.join(WEBAPP_ROOT, 'public');
const BUILDS_DIR = path.join(PUBLIC_DIR, 'data/builds');
const OUTPUT_DIR = path.join(PUBLIC_DIR, 'social-previews/builds');

// Color scheme by class (matching config)
const COLOR_SCHEME = {
  barbarian: '#8B4513',
  druid: '#228B22',
  necromancer: '#2F4F4F',
  paladin: '#FFD700',
  rogue: '#B22222',
  sorcerer: '#4169E1',
  spiritborn: '#8B008B',
};

/**
 * Parse build filename to extract class
 */
function getClassFromFilename(filename) {
  const match = filename.match(/^([a-z]+)_/);
  return match ? match[1] : 'barbarian';
}

/**
 * Create SVG HTML for the preview image
 */
function createSVG(buildData, className, buildId) {
  const accentColor = COLOR_SCHEME[className] || '#8B4513';

  // Truncate text if needed
  const buildName = buildData.build_name.substring(0, 50);
  const playstyle = buildData.playstyle_summary.substring(0, 120);
  const difficulty = buildData.difficulty || 'Unknown';

  const svg = `
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 630" width="1200" height="630">
  <defs>
    <linearGradient id="bgGradient" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" style="stop-color:#1a1a1a;stop-opacity:1" />
      <stop offset="100%" style="stop-color:#0d0d0d;stop-opacity:1" />
    </linearGradient>
  </defs>

  <!-- Background -->
  <rect width="1200" height="630" fill="url(#bgGradient)" />

  <!-- Accent bar -->
  <rect width="8" height="630" fill="${accentColor}" />

  <!-- Content area -->
  <g transform="translate(40, 50)">
    <!-- Class label -->
    <text x="0" y="0" font-family="Cinzel, serif" font-size="32" font-weight="700" fill="${accentColor}">
      ${className.toUpperCase()}
    </text>

    <!-- Build name -->
    <text x="0" y="80" font-family="Cinzel, serif" font-size="56" font-weight="700" fill="#ffffff">
      ${buildName}
    </text>

    <!-- Playstyle description -->
    <text x="0" y="150" font-family="EB Garamond, serif" font-size="22" fill="#cccccc">
      ${playstyle}
    </text>

    <!-- Meta info -->
    <g transform="translate(0, 300)">
      <text x="0" y="0" font-family="Roboto Condensed, sans-serif" font-size="18" fill="#999999">
        Difficulty: <tspan fill="#ffffff">${difficulty}</tspan>
      </text>
      <text x="0" y="40" font-family="Roboto Condensed, sans-serif" font-size="18" fill="#999999">
        Build ID: <tspan fill="#ffffff" font-family="monospace">${buildId}</tspan>
      </text>
    </g>
  </g>

  <!-- D4Builder branding (bottom right) -->
  <g transform="translate(1050, 580)">
    <text x="0" y="0" font-family="Cinzel, serif" font-size="16" fill="#666666" text-anchor="end">
      d4builder.com
    </text>
  </g>
</svg>
  `.trim();

  return svg;
}

/**
 * Create HTML file from SVG for rendering
 */
function createHTMLFromSVG(svg) {
  return `
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    body { margin: 0; padding: 0; }
  </style>
</head>
<body>
  ${svg}
</body>
</html>
  `.trim();
}

/**
 * Generate preview image using Puppeteer
 */
async function generatePreviewImage(browser, buildData, buildId, className, outputPath) {
  let page = null;
  try {
    page = await browser.newPage();

    // Set viewport to match image dimensions
    await page.setViewport({ width: 1200, height: 630 });

    const svg = createSVG(buildData, className, buildId);
    const html = createHTMLFromSVG(svg);

    // Load HTML content
    await page.setContent(html);

    // Create directory if it doesn't exist
    fs.mkdirSync(path.dirname(outputPath), { recursive: true });

    // Take screenshot
    await page.screenshot({
      path: outputPath,
      type: 'png',
      omitBackground: false,
    });

    return true;
  } catch (error) {
    console.error(`❌ Failed to generate ${buildId}: ${error.message}`);
    return false;
  } finally {
    if (page) {
      await page.close();
    }
  }
}

/**
 * Main generation function
 */
async function generateAllPreviews() {
  console.log('🎨 Generating social preview images with Puppeteer...\n');

  const buildFiles = fs.readdirSync(BUILDS_DIR).filter((f) => f.endsWith('.json'));

  let success = 0;
  let failed = 0;
  let browser = null;

  try {
    browser = await puppeteer.launch({ headless: 'new' });

    for (const filename of buildFiles) {
      const buildId = filename.replace('.json', '');
      const className = getClassFromFilename(buildId);

      try {
        const buildPath = path.join(BUILDS_DIR, filename);
        const buildData = JSON.parse(fs.readFileSync(buildPath, 'utf-8'));

        const outputPath = path.join(OUTPUT_DIR, className, `${buildId}.png`);

        const result = await generatePreviewImage(browser, buildData, buildId, className, outputPath);

        if (result) {
          console.log(`✅ Generated: ${className}/${buildId}.png`);
          success++;
        } else {
          failed++;
        }
      } catch (error) {
        console.error(`❌ Error processing ${buildId}: ${error.message}`);
        failed++;
      }
    }
  } finally {
    if (browser) {
      await browser.close();
    }
  }

  console.log(`\n📊 Generation Summary:`);
  console.log(`   ✅ Success: ${success}`);
  console.log(`   ❌ Failed: ${failed}`);
  console.log(`   📁 Output: ${OUTPUT_DIR}\n`);

  if (failed === 0) {
    console.log('🎉 All social preview images generated successfully!');
  }

  return failed === 0;
}

// Run if executed directly
generateAllPreviews().catch(console.error);
