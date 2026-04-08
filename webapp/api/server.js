import express from 'express';
import Database from 'better-sqlite3';
import { readFileSync, existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { randomUUID } from 'crypto';

const __dirname = dirname(fileURLToPath(import.meta.url));

const DB_PATH    = join(__dirname, 'builds_cache.db');
const BUILDS_DIR = join(__dirname, '../public/data/builds');
const INDEX_PATH = join(__dirname, '../public/data/builds_index.json');

const db = new Database(DB_PATH);

function initSchema() {
  db.exec(`
    CREATE TABLE IF NOT EXISTS builds (
      id               TEXT PRIMARY KEY,
      uuid             TEXT NOT NULL,
      build_name       TEXT,
      class            TEXT,
      available        TEXT,
      season           TEXT,
      difficulty       TEXT,
      playstyle_summary TEXT,
      stat_priority    TEXT,
      file             TEXT,
      guide            TEXT,
      tier             TEXT,
      efficiency_score REAL,
      season_rank      INTEGER,
      build_data       TEXT
    )
  `);
}

function seedFromJson() {
  const count = db.prepare('SELECT COUNT(*) as n FROM builds').get().n;
  if (count > 0) {
    console.log('[api] DB has', count, 'builds — skipping seed');
    return;
  }

  const index = JSON.parse(readFileSync(INDEX_PATH, 'utf8'));
  const insert = db.prepare(`
    INSERT INTO builds (
      id, uuid, build_name, class, available, season, difficulty,
      playstyle_summary, stat_priority, file, guide,
      tier, efficiency_score, season_rank, build_data
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `);

  db.transaction(() => {
    for (const build of index.builds) {
      const buildFile = join(BUILDS_DIR, `${build.id}.json`);
      const buildData = existsSync(buildFile) ? readFileSync(buildFile, 'utf8') : null;
      insert.run(
        build.id,
        build.uuid || randomUUID(),
        build.build_name,
        build.class,
        build.available,
        build.season,
        build.difficulty,
        build.playstyle_summary,
        JSON.stringify(build.stat_priority),
        build.file,
        build.guide,
        build.tier ?? null,
        build.efficiency_score ?? null,
        build.season_rank ?? null,
        buildData,
      );
    }
  })();

  console.log('[api] seeded', index.builds.length, 'builds from JSON (first run only)');
}

initSchema();
seedFromJson();

const app = express();

app.get('/api/builds', (_req, res) => {
  const rows = db.prepare(`
    SELECT id, uuid, build_name, class, available, season, difficulty,
           playstyle_summary, stat_priority, file, guide,
           tier, efficiency_score, season_rank
    FROM builds
    ORDER BY season_rank ASC
  `).all();

  const builds = rows.map(r => ({
    ...r,
    stat_priority: r.stat_priority ? JSON.parse(r.stat_priority) : [],
  }));

  const classes = [...new Set(builds.map(b => b.class))].sort();

  res.json({
    version: '1.0',
    season: builds[0]?.season ?? '',
    total_builds: builds.length,
    classes,
    builds,
  });
});

app.get('/api/builds/:uuid', (req, res) => {
  const row = db.prepare(
    `SELECT build_data FROM builds WHERE uuid = ? OR id = ?`
  ).get(req.params.uuid, req.params.uuid);
  if (!row) return res.status(404).json({ error: 'Build not found' });
  if (!row.build_data) return res.status(404).json({ error: 'Build data not found' });
  res.type('json').send(row.build_data);
});

const PORT = 3001;
app.listen(PORT, () => console.log(`[api] listening on http://localhost:${PORT}`));
