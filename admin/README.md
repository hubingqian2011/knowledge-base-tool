# Knowledge Base Admin

A general-purpose knowledge base management tool built with React + Ant Design + Vite.

## Development

```bash
cd admin
npm install --legacy-peer-deps
npm run dev
# visit http://localhost:3001
```

API requests in dev mode are proxied by Vite to the backend at `http://localhost:10090`.

## Build

```bash
npm run build
# output in dist/
```

## Docker

```bash
cd admin
docker build -t kb-admin .
docker run -d --name kb-admin -p 3001:3001 kb-admin
```

## Tech Stack

| Package | Version |
|---------|---------|
| React | 19 |
| Ant Design | 6.x |
| Vite | 8.x |
| react-router-dom | 7.x |
| Axios | 1.x |

## Backend

The admin UI connects to `/api/admin/*` routes provided by the FastAPI backend.
