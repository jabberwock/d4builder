import type { BuildSummary } from '../types';

const BASE_URL = typeof window !== 'undefined' ? window.location.origin : 'https://d4builder.com';
const SOCIAL_PREVIEW_BASE = '/social-previews/builds';

function escapeHtml(text: string): string {
  return text.replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' }[c] ?? c)
  );
}

function setMeta(property: string, content: string, useProperty = true): void {
  const attr = useProperty ? 'property' : 'name';
  const selector = `meta[${attr}="${property}"]`;
  let el = document.querySelector(selector) as HTMLMetaElement | null;
  if (!el) {
    el = document.createElement('meta');
    el.setAttribute(attr, property);
    document.head.appendChild(el);
  }
  el.content = escapeHtml(content);
}

function setCanonical(href: string): void {
  let link = document.querySelector('link[rel="canonical"]') as HTMLLinkElement | null;
  if (!link) {
    link = document.createElement('link');
    link.rel = 'canonical';
    document.head.appendChild(link);
  }
  link.href = href;
}

export function updateOGTagsEffect(build: BuildSummary): void {
  const classLower = build.class.toLowerCase();
  const imageUrl = `${BASE_URL}${SOCIAL_PREVIEW_BASE}/${classLower}/${build.id}.png`;
  const canonicalUrl = `${BASE_URL}/#${build.uuid}`;
  const title = `${build.build_name} \u2022 ${build.class} Build`;
  const description = `${build.class} ${build.difficulty} build: ${build.playstyle_summary.substring(0, 120)}`;

  setMeta('og:type', 'website');
  setMeta('og:title', title);
  setMeta('og:description', description);
  setMeta('og:image', imageUrl);
  setMeta('og:image:width', '1200');
  setMeta('og:image:height', '630');
  setMeta('og:url', canonicalUrl);
  setMeta('twitter:card', 'summary_large_image');
  setMeta('twitter:title', title);
  setMeta('twitter:description', description);
  setMeta('twitter:image', imageUrl);
  setMeta('description', description, false);

  setCanonical(canonicalUrl);
  document.title = `${build.build_name} — D4 Builder`;
}

export function resetOGTags(): void {
  const defaultTitle = 'D4 Builder \u2014 Season 12 Build Guide';
  const defaultDesc = 'Diablo 4 season build guides for all classes. Find top-tier builds with skill trees, gear, and stat priorities.';

  setMeta('og:title', defaultTitle);
  setMeta('og:description', defaultDesc);
  setMeta('og:url', `${BASE_URL}/`);
  setMeta('twitter:title', defaultTitle);
  setMeta('twitter:description', defaultDesc);
  setMeta('description', defaultDesc, false);

  setCanonical(`${BASE_URL}/`);
  document.title = defaultTitle;
}
