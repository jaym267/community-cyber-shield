# Deploying EJMapper

Two pieces: the **FastAPI backend → Render** and the **React frontend → Vercel**.
Deploy the backend first, then point the frontend at it.

---

## 1. Backend → Render (free tier)

1. Push this repo to GitHub (already done).
2. Go to <https://render.com> → **New → Blueprint** → connect this repo.
   Render auto-detects [`render.yaml`](render.yaml) and creates the `ejmapper-api`
   web service (Python, root `ejm-apper/backend`).
3. When prompted, set the secret env vars (these are **not** in git):
   - `ANTHROPIC_API_KEY` → your Anthropic key
   - `ALLOWED_ORIGINS` → leave blank for now (you'll add the Vercel URL after step 2,
     and only if it's a custom domain — `*.vercel.app` is already allowed).
4. Click **Apply**. Wait for the build, then note the live URL, e.g.
   `https://ejmapper-api.onrender.com`.
5. Verify: open `https://<your-render-url>/api/health` → should return
   `{"status":"ok",...}`.

> Note: Render's free tier sleeps after inactivity, so the first request after idle
> can take ~30–60s to wake up. That's expected.

---

## 2. Frontend → Vercel (free tier)

1. Go to <https://vercel.com> → **Add New → Project** → import this repo.
2. **Important — set the Root Directory to `ejm-apper/frontend`** (the app isn't at
   the repo root). Vercel auto-detects Vite (build `npm run build`, output `dist`).
3. Add Environment Variables (Settings → Environment Variables):
   - `VITE_MAPBOX_TOKEN` → your Mapbox `pk.` token
   - `VITE_API_BASE` → the Render backend URL from step 1
     (e.g. `https://ejmapper-api.onrender.com`, no trailing slash)
   > Vite inlines env vars at **build time**, so these must be set before/at deploy.
   > If you change them later, redeploy.
4. Deploy. You'll get a URL like `https://community-cyber-shield.vercel.app`.
5. [`vercel.json`](ejm-apper/frontend/vercel.json) rewrites all routes to
   `index.html`, so shareable links like `https://<your-app>.vercel.app/78207`
   load that report directly.

---

## 3. Final wiring

- CORS already allows any `*.vercel.app` origin (see `main.py`), so the default
  Vercel domain works out of the box.
- If you add a **custom domain**, append it to `ALLOWED_ORIGINS` in Render
  (comma-separated) and redeploy the backend.

## Local development (unchanged)

```bash
# backend
cd ejm-apper/backend && uvicorn main:app --reload   # http://127.0.0.1:8000

# frontend (new terminal)
cd ejm-apper/frontend && npm run dev                 # http://localhost:5173
```

The frontend falls back to `http://127.0.0.1:8000` when `VITE_API_BASE` is unset.

---

## 4. Security headers & Content-Security-Policy

The frontend ships security headers (incl. a Content-Security-Policy) via
[`vercel.json`](ejm-apper/frontend/vercel.json), and the backend sets its own
headers in `main.py`. Two things to know:

- **CSP `connect-src` allows `https://*.onrender.com` and `https://*.mapbox.com`.**
  If you host the backend somewhere other than Render's default `*.onrender.com`
  domain (e.g. a custom domain), add that origin to the `connect-src` directive
  in `vercel.json`, or the frontend's API calls will be blocked by the browser.
- After the first deploy, open the site, hit a search, and check the browser
  console for any `Content-Security-Policy` violation warnings. If the map or
  data fails to load, a blocked origin in `connect-src`/`img-src` is the usual
  cause — add it and redeploy.

> The legal pages (Disclaimer / Privacy / Terms) in the footer are plain-English
> templates. Have a lawyer review them before a serious public launch.
