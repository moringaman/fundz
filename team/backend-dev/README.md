# Team Member: Backend Dev
## Role: Senior Python Backend Developer

### Responsibility
Build the FastAPI backend, database models, Phemex API clients, services, and all Python logic for the trading application.

### Current Tasks (Phase 1)
- [ ] Create environment configuration (.env.example)
- [ ] Create .gitignore
- [ ] Set up SQLAlchemy database models
- [ ] Create FastAPI app with health endpoint
- [ ] Set up Docker Compose

### Tech Stack
- Python 3.11+
- FastAPI
- SQLAlchemy + asyncpg
- PostgreSQL
- WebSockets (websockets library)
- python-dotenv
- pydantic
- httpx

### Key Files to Create
```
backend/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI app entry
│   ├── config.py            # Settings/config
│   ├── database.py          # DB connection
│   ├── models/              # SQLAlchemy models
│   │   ├── __init__.py
│   │   ├── user.py
│   │   ├── agent.py
│   │   ├── trade.py
│   │   ├── position.py
│   │   └── balance.py
│   ├── schemas/             # Pydantic schemas
│   ├── api/
│   │   └── routes/          # API endpoints
│   ├── services/            # Business logic
│   └── clients/             # External API clients
├── requirements.txt
├── Dockerfile
└── .env.example
```

### Conventions
- Async/await for all I/O operations
- Pydantic for all data validation
- Structured logging (JSON)
- Type hints everywhere
- Follows: https://fastapi.tiangolo.com/tutorial/

### Contact
Escalate to: Tech Lead / Architect
