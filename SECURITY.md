# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in EJMapper, please report it
privately so it can be fixed before public disclosure.

- **Preferred:** Open a private report via GitHub's
  ["Report a vulnerability"](https://github.com/jaym267/community-cyber-shield/security/advisories/new)
  feature on this repository.
- Please do **not** open a public issue for security problems.

Include as much detail as you can: the affected URL or endpoint, steps to
reproduce, and the potential impact. We aim to acknowledge reports within a few
days.

## Scope

This is a small public-interest project. In scope: the web frontend, the
FastAPI backend, and their configuration. Out of scope: vulnerabilities in
third-party services we depend on (Mapbox, the U.S. EPA, OpenStreetMap,
Vercel, Render) — please report those to the respective provider.

## Good to know

- The app does not collect accounts or personal data.
- Secrets (API keys) are kept in environment variables and are never committed
  to this repository.
- The backend applies input validation, per-IP rate limiting, and security
  headers; the frontend ships a Content-Security-Policy. Reports that help
  strengthen these are welcome.
