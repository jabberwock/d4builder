#!/usr/bin/env node
/**
 * Scrape Maxroll D4 skill tree planner to extract every skill/passive
 * and its row position. Uses Playwright to render the page and hover
 * over each node to get tooltip data.
 *
 * Output: data/skill_tree_positions.json
 *   { "class": { "skill_name": { row, section, points_required } } }
 */

import { chromium } from 'playwright';
import { writeFileSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));

// Maxroll skill tree planner URLs per class
const CLASS_URLS = {
  Barbarian:   'https://maxroll.gg/d4/planner#c=barbarian',
  Druid:       'https://maxroll.gg/d4/planner#c=druid',
  Necromancer: 'https://maxroll.gg/d4/planner#c=necromancer',
  Rogue:       'https://maxroll.gg/d4/planner#c=rogue',
  Sorcerer:    'https://maxroll.gg/d4/planner#c=sorcerer',
  Spiritborn:  'https://maxroll.gg/d4/planner#c=spiritborn',
  Paladin:     'https://maxroll.gg/d4/planner#c=paladin',
};

// In-game point thresholds per row (1-indexed)
const ROW_POINTS = { 1: 0, 2: 1, 3: 2, 4: 11, 5: 16, 6: 23, 7: 33 };

async function scrapeClass(browser, className, url) {
  const page = await browser.newPage();
  console.log(`  [${className}] Loading ${url}...`);

  await page.goto(url, { waitUntil: 'networkidle', timeout: 30000 });

  // Wait for skill tree to render
  await page.waitForSelector('.d4t-SkillPreview, .d4t-TreeGraph', { timeout: 15000 }).catch(() => {
    console.log(`  [${className}] No skill tree found, trying expanded view...`);
  });

  // Try to find the condensed view (d4t-SkillPreview with d4t-skill-group rows)
  const hasCondensed = await page.$('.d4t-SkillPreview .d4t-skill-group');

  let results = {};

  if (hasCondensed) {
    console.log(`  [${className}] Using condensed view...`);
    results = await scrapeCondensedView(page, className);
  } else {
    // Try expanded tree view
    console.log(`  [${className}] Using expanded view, hovering nodes...`);
    results = await scrapeExpandedView(page, className);
  }

  await page.close();
  return results;
}

async function scrapeCondensedView(page, className) {
  const results = {};

  // Get all skill groups (rows)
  const groups = await page.$$('.d4t-skill-group');
  console.log(`  [${className}] Found ${groups.length} rows`);

  for (let rowIdx = 0; rowIdx < groups.length; rowIdx++) {
    const row = rowIdx + 1;
    const pts = ROW_POINTS[row] ?? 16;
    const group = groups[rowIdx];

    // Get all nodes in this row — hover each to get the tooltip
    const nodes = await group.$$('.d4t-skill-frame, .d4t-passive-frame');

    for (const node of nodes) {
      try {
        // Hover to trigger tooltip
        await node.hover({ timeout: 2000 });
        await page.waitForTimeout(150);

        // Read tooltip
        const tooltip = await page.$('.d4t-Tooltip, .d4-tooltip, [class*="tooltip"]');
        if (tooltip) {
          const name = await tooltip.$eval(
            '.d4t-skill-name, .d4t-header, [class*="name"]',
            el => el.textContent?.trim()
          ).catch(() => null);

          if (name && name.length > 1 && !name.startsWith('?')) {
            results[name.toLowerCase()] = {
              name,
              row,
              points_required: pts,
            };
          }
        }
      } catch (e) {
        // Skip nodes that can't be hovered
      }
    }

    console.log(`  [${className}] Row ${row} (${pts}pts): ${Object.keys(results).length} skills so far`);
  }

  return results;
}

async function scrapeExpandedView(page, className) {
  const results = {};

  // In expanded view, nodes have absolute positions
  // Hover each node to get tooltip with skill name
  const nodes = await page.$$('.d4t-node');
  console.log(`  [${className}] Found ${nodes.length} nodes in expanded view`);

  for (let i = 0; i < nodes.length; i++) {
    try {
      const node = nodes[i];
      await node.hover({ timeout: 2000 });
      await page.waitForTimeout(150);

      // Try to read tooltip
      const tooltipText = await page.$eval(
        '.d4t-Tooltip .d4t-skill-name, .d4t-Tooltip [class*="name"]',
        el => el.textContent?.trim()
      ).catch(() => null);

      if (tooltipText && tooltipText.length > 1) {
        // Get Y position for row estimation
        const box = await node.boundingBox();
        if (box) {
          results[tooltipText.toLowerCase()] = {
            name: tooltipText,
            y: Math.round(box.y),
          };
        }
      }
    } catch (e) {
      // Skip
    }

    if (i % 20 === 0) {
      console.log(`  [${className}] Processed ${i}/${nodes.length} nodes...`);
    }
  }

  return results;
}

async function main() {
  console.log('Launching browser...');
  const browser = await chromium.launch({ headless: true });

  const allResults = {};

  for (const [className, url] of Object.entries(CLASS_URLS)) {
    try {
      allResults[className] = await scrapeClass(browser, className, url);
      console.log(`  [${className}] Done: ${Object.keys(allResults[className]).length} skills\n`);
    } catch (e) {
      console.log(`  [${className}] ERROR: ${e.message}\n`);
      allResults[className] = {};
    }
  }

  await browser.close();

  const outPath = join(__dirname, 'skill_tree_positions.json');
  writeFileSync(outPath, JSON.stringify(allResults, null, 2));
  console.log(`\nWrote ${outPath}`);

  // Summary
  for (const [cls, skills] of Object.entries(allResults)) {
    console.log(`  ${cls}: ${Object.keys(skills).length} skills`);
  }
}

main().catch(console.error);
