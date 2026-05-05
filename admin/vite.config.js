import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('node_modules/@js-preview/pdf')) return 'pdfPreview';
          if (id.includes('node_modules/@ant-design/icons')) return 'antIcons';
          if (id.includes('node_modules/react-router-dom')) return 'router';
          if (id.includes('node_modules/react') || id.includes('node_modules/react-dom')) return 'react';
          return undefined;
        },
      },
    },
  },
  server: {
    port: 3001,
    proxy: {
      '/api': {
        target: 'http://localhost:10090',
        changeOrigin: true,
      },
    },
  },
});
