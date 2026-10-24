import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
// Inter from the package, not from a CDN: the app works without the internet
import '@fontsource/inter/700.css'
import '@fontsource/inter/400.css'
import '@fontsource/inter/500.css'
import '@fontsource/inter/600.css'
import { App } from './App'
import './theme.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
