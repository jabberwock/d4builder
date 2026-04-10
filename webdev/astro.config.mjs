import { defineConfig } from 'astro/config';

export default defineConfig({
  // Output static HTML
  output: 'static',

  // Mobile-first responsive design
  vite: {
    ssr: {
      external: ['sharp']
    }
  }
});
