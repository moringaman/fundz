# Team Member: DevOps
## Role: Infrastructure & DevOps Engineer

### Responsibility
Configure Docker, deployment pipelines, CI/CD, infrastructure, and ensure the application runs smoothly in containerized environments.

### Current Tasks (Phase 1)
- [ ] Create docker-compose.yml
- [ ] Configure PostgreSQL service
- [ ] Set up Redis (for caching/WebSocket pub/sub)
- [ ] Create Dockerfiles for frontend/backend
- [ ] Environment configuration

### Tech Stack
- Docker & Docker Compose
- PostgreSQL 15+
- Redis
- GitHub Actions (CI/CD)
- Nginx (reverse proxy)

### Key Files to Create
```
docker-compose.yml
Dockerfile.backend
Dockerfile.frontend
.github/workflows/
  └── ci.yml
deploy/
  ├── nginx.conf
  └── systemd.service
```

### Conventions
- Multi-stage Docker builds for optimization
- Health checks on all services
- Secrets via environment variables (never commit .env)
- Non-root users in containers

### Contact
Escalate to: Backend Dev / Frontend Dev
