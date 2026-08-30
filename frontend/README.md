# Frontend

Next.js (App Router) web interface for the Multimodal Hybrid-Retrieval RAG Framework. Provides document upload, chat-style querying, citation display, live pipeline-stage progress, and per-query token and cost reporting.

Requirements covered: FRONTEND-01 to FRONTEND-05. See [02. Requirements Specification](../docs/02-requirements-specification.md).

## Stack

| Component | Role |
|---|---|
| Next.js (App Router) | Application framework |
| React with TypeScript | Component model and type safety across the API boundary |
| Tailwind CSS | Styling |

TypeScript types mirror the backend Pydantic models, in particular the citation union, so that branching on citation type is checked at compile time.

## Prerequisites

- Node.js 18.18 or later
- A running backend service (see the repository root README)

## Configuration

Copy the example environment file and set the backend base URL:

```bash
cp .env.local.example .env.local
```

| Variable | Purpose |
|---|---|
| `NEXT_PUBLIC_API_BASE_URL` | Base URL of the backend service |

No model-provider credentials are used by the frontend. All provider credentials remain server-side; the backend base URL is the only value exposed to the client.

## Development

```bash
npm install
npm run dev
```

The development server runs at http://localhost:3000.

## Build

```bash
npm run build
npm run start
```

## Notes

Live pipeline progress and token usage arrive over a single streaming response in Server-Sent Events format, read through a streaming `fetch` rather than the browser `EventSource` API, because the query request carries a JSON body and `EventSource` issues only GET requests. No additional client library is required.
