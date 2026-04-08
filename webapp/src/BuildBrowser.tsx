import { createSignal, createResource, createEffect, untrack, For, Show, onCleanup } from 'solid-js';
import type { Component } from 'solid-js';
import type { BuildsIndex, BuildSummary } from './types';
import BuildDetail from './BuildDetail';
import { updateOGTagsEffect, resetOGTags } from './utils/og-tags';

/* ─── Helpers ─────────────────────────────────────────────────────────── */

function classSlug(cls: string): string {
  return cls.toLowerCase().replace(/\s+/g, '-');
}

function classCssClass(cls: string): string {
  return `cls-${cls.toLowerCase()}`;
}

/* Ember particle definitions (static, deterministic) */
const EMBERS = [
  { left: '8%',  size: 3, dur: '3.2s', delay: '0s',    drift: '12px'  },
  { left: '18%', size: 2, dur: '2.5s', delay: '0.7s',  drift: '-8px'  },
  { left: '28%', size: 4, dur: '3.8s', delay: '1.4s',  drift: '18px'  },
  { left: '38%', size: 2, dur: '2.9s', delay: '0.3s',  drift: '-14px' },
  { left: '48%', size: 3, dur: '3.4s', delay: '1.9s',  drift: '10px'  },
  { left: '55%', size: 2, dur: '2.7s', delay: '0.9s',  drift: '-10px' },
  { left: '63%', size: 4, dur: '3.6s', delay: '2.2s',  drift: '16px'  },
  { left: '72%', size: 3, dur: '3.1s', delay: '0.5s',  drift: '-12px' },
  { left: '80%', size: 2, dur: '2.8s', delay: '1.7s',  drift: '8px'   },
  { left: '89%', size: 3, dur: '3.3s', delay: '1.1s',  drift: '-16px' },
  { left: '22%', size: 2, dur: '4.0s', delay: '2.5s',  drift: '6px'   },
  { left: '58%', size: 3, dur: '3.7s', delay: '3.0s',  drift: '-20px' },
];

/* ─── Data fetching ───────────────────────────────────────────────────── */

async function fetchIndex(): Promise<BuildsIndex> {
  const res = await fetch('/api/builds');
  if (!res.ok) throw new Error('Failed to load builds index');
  return res.json() as Promise<BuildsIndex>;
}

/* ─── Sub-components ──────────────────────────────────────────────────── */

const RuneDivider: Component = () => (
  <div class="rune-divider" aria-hidden="true">
    <span class="line" />
    <span class="diamond-sm" />
    <span class="diamond" />
    <span class="diamond-sm" />
    <span class="line" />
  </div>
);

/* ─── Main component ──────────────────────────────────────────────────── */

const BuildBrowser: Component = () => {
  const [index] = createResource(fetchIndex);
  const [selectedClass, setSelectedClass] = createSignal<string>('All');
  const [selectedTier, setSelectedTier] = createSignal<string>('All');
  const [selectedBuild, setSelectedBuild] = createSignal<BuildSummary | null>(null);
  const [buildNotFound, setBuildNotFound] = createSignal(false);

  /* ── Hash routing ─────────────────────────────────────────────────── */

  // Track whether we've processed the initial URL hash
  let initialHashProcessed = false;

  function selectBuildByUuid(uuid: string): void {
    const data = index();
    if (!data) return;
    // Support UUID lookup; fall back to slug for legacy links
    const match = data.builds.find(b => b.uuid === uuid) ?? data.builds.find(b => b.id === uuid);
    if (match) {
      setSelectedBuild(match);
      setBuildNotFound(false);
    } else {
      // Stale or invalid UUID — clear the hash and show an error only after initial routing
      if (initialHashProcessed) {
        window.history.replaceState(null, '', window.location.pathname);
      }
      setBuildNotFound(true);
    }
  }

  // URL → state (on load + index ready)
  createEffect(() => {
    const data = index();
    if (!data) return;
    const id = window.location.hash.slice(1);
    if (id) selectBuildByUuid(id);
    initialHashProcessed = true;
  });

  // Sync state → URL — only after initial hash has been processed to avoid
  // wiping the hash before the index finishes loading
  createEffect(() => {
    const build = selectedBuild();
    if (!untrack(index)) return;
    if (!initialHashProcessed) return;
    const hash = build ? `#${build.uuid}` : '';
    if (window.location.hash !== hash) {
      window.history.pushState(null, '', hash || window.location.pathname);
    }
  });

  // Update OG meta tags on build selection
  createEffect(() => {
    const build = selectedBuild();
    if (build) {
      updateOGTagsEffect(build);
    } else {
      resetOGTags();
    }
  });

  // Handle browser back/forward
  function onPopState(): void {
    const id = window.location.hash.slice(1);
    if (id) {
      selectBuildByUuid(id);
    } else {
      setSelectedBuild(null);
    }
  }
  window.addEventListener('popstate', onPopState);
  onCleanup(() => window.removeEventListener('popstate', onPopState));

  const filtered = () => {
    const data = index();
    if (!data) return [];
    return data.builds
      .filter(b => {
        const classOk = selectedClass() === 'All' || b.class === selectedClass();
        const tierOk  = selectedTier()  === 'All' || b.tier  === selectedTier();
        return classOk && tierOk;
      })
      .sort((a, b) => (a.season_rank ?? 99) - (b.season_rank ?? 99));
  };

  return (
    <div class="codex">
      <Show
        when={selectedBuild()}
        fallback={
          <>
            {/* ── Header ──────────────────────────────────── */}
            <header class="codex-header">
              <div class="ember-field" aria-hidden="true">
                <For each={EMBERS}>
                  {e => (
                    <span
                      class="ember"
                      style={`left:${e.left};width:${e.size}px;height:${e.size}px;--dur:${e.dur};--delay:${e.delay};--drift:${e.drift}`}
                    />
                  )}
                </For>
              </div>

              <div class="header-inner">
                <RuneDivider />
                <h1 class="codex-wordmark">D4 Builder</h1>
                <RuneDivider />
                <p class="codex-season">
                  <em>{index()?.season ?? 'Season 12 — Season of Slaughter'}</em>
                  &ensp;·&ensp;
                  {index()?.total_builds ?? 0} Builds
                </p>
              </div>
            </header>

            {/* ── Filters ─────────────────────────────────── */}
            <section class="filter-bar" aria-label="Build filters">
              {/* Class */}
              <div class="filter-row">
                <span class="filter-label">Class</span>
                <div class="filter-pills" role="group" aria-label="Filter by class">
                  <button
                    class={`pill${selectedClass() === 'All' ? ' active' : ''}`}
                    onClick={() => setSelectedClass('All')}
                  >
                    All
                  </button>
                  <For each={index()?.classes ?? []}>
                    {cls => (
                      <button
                        class={`pill ${classCssClass(cls)}${selectedClass() === cls ? ' active' : ''}`}
                        onClick={() => setSelectedClass(cls)}
                      >
                        {cls}
                      </button>
                    )}
                  </For>
                </div>
              </div>

              {/* Tier */}
              <div class="filter-row">
                <span class="filter-label">Tier</span>
                <div class="filter-pills" role="group" aria-label="Filter by tier">
                  {(['All', 'S', 'A', 'B', 'C'] as const).map(t => (
                    <button
                      class={`pill tier-pill tier-${t.toLowerCase()}${selectedTier() === t ? ' active' : ''}`}
                      onClick={() => setSelectedTier(t)}
                    >
                      {t}
                    </button>
                  ))}
                </div>
              </div>
            </section>

            {/* ── Error ───────────────────────────────────── */}
            <Show when={index.error}>
              <div class="error-banner" role="alert">
                Failed to load builds — check the dev server is running.
              </div>
            </Show>
            <Show when={buildNotFound()}>
              <div class="error-banner" role="alert">
                Build not found — this shared link may be outdated.
              </div>
            </Show>

            {/* ── Grid ────────────────────────────────────── */}
            <div class="build-grid-wrap">
              <Show when={!index.loading} fallback={
                <div class="loading-state">Summoning the Codex…</div>
              }>
                <p class="grid-count">
                  {filtered().length} build{filtered().length !== 1 ? 's' : ''} found
                </p>
                <div class="build-grid" role="list">
                  <For each={filtered()} fallback={
                    <div class="empty-state">No builds match the selected filters.</div>
                  }>
                    {(build, i) => (
                      <button
                        class="build-card"
                        role="listitem"
                        style={`animation-delay: ${Math.min(i() * 0.04, 0.5)}s`}
                        onClick={() => { setSelectedBuild(build); setBuildNotFound(false); }}
                        aria-label={`${build.build_name} — ${build.class}`}
                      >
                        {/* Hero image */}
                        <div class="card-hero-wrap">
                          <img
                            src={`/media/classes/${classSlug(build.class)}.webp`}
                            alt=""
                            class="card-hero"
                            loading="lazy"
                          />
                          <div class="card-hero-overlay" />
                        </div>

                        {/* Body */}
                        <div class="card-body">
                          <div class="card-meta">
                            <span class={`card-class ${classCssClass(build.class)}`}>
                              {build.class}
                            </span>
                          </div>
                          <h3 class="card-name">{build.build_name}</h3>
                          <p class="card-summary">{build.playstyle_summary}</p>
                        </div>

                        {/* Footer */}
                        <div class="card-footer">
                          <span class={`card-tier tier-${(build.tier ?? 'c').toLowerCase()}`}>
                            {build.tier ?? '?'} Tier
                          </span>
                          <Show when={build.season_rank}>
                            <span class="card-season-rank">#{build.season_rank}</span>
                          </Show>
                        </div>
                      </button>
                    )}
                  </For>
                </div>
              </Show>
            </div>
          </>
        }
      >
        <BuildDetail
          summary={selectedBuild()!}
          onBack={() => {
            setSelectedBuild(null);
            window.history.pushState(null, '', window.location.pathname);
          }}
        />
      </Show>
    </div>
  );
};

export default BuildBrowser;
