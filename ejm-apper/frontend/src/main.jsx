import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'
import EmbedWidget from './EmbedWidget.jsx'

// Embed route: /embed/{zip} renders a compact, iframe-friendly badge instead
// of the full app (see EmbedWidget.jsx). ?lang=es is honored.
const embedMatch = window.location.pathname.match(/^\/embed\/(\d{5})/)
const lang = new URLSearchParams(window.location.search).get('lang') === 'es' ? 'es' : 'en'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    {embedMatch
      ? <EmbedWidget zip={embedMatch[1]} lang={lang} />
      : <App />}
  </StrictMode>,
)
