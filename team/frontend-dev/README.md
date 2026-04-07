# Team Member: Frontend Dev
## Role: React/TypeScript Frontend Developer

### Responsibility
Build the React dashboard, trading charts, agent configuration UI, and all frontend components for the trading application.

### Current Tasks (Phase 1)
- Waiting on backend setup (Phase 1.5+)
- Will initialize React project once backend scaffolding is ready

### Tech Stack
- React 18+ with TypeScript
- Vite (build tool)
- Tailwind CSS
- Radix UI / shadcn/ui
- lightweight-charts (TradingView)
- TanStack Query (data fetching)
- Zustand (state management)
- React Router 6

### Key Files to Create (Future)
```
frontend/
├── src/
│   ├── components/
│   │   ├── ui/              # Base UI components
│   │   ├── charts/          # TradingView charts
│   │   ├── agents/          # Agent management
│   │   └── trading/         # Order forms, etc.
│   ├── pages/
│   │   ├── Dashboard.tsx
│   │   ├── Trading.tsx
│   │   ├── Agents.tsx
│   │   └── Settings.tsx
│   ├── hooks/               # Custom React hooks
│   ├── services/            # API clients
│   ├── stores/              # Zustand stores
│   └── types/               # TypeScript types
├── index.html
├── vite.config.ts
├── tailwind.config.js
├── tsconfig.json
└── package.json
```

### Conventions
- Component-driven development
- Strict TypeScript (no `any`)
- Tailwind for styling (use shadcn/ui components)
- TanStack Query for server state
- Functional components + hooks

### Contact
Escalate to: Backend Dev (for API contracts)
