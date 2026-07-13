import { useEffect } from 'react'
import { useLocation } from 'react-router-dom'

const TITLES = {
  '/':                 'CivicOS — Open Government Building Blocks',
  '/building-blocks':  'Building Blocks — CivicOS',
  '/about':            'About — CivicOS',
  '/contact':          'Contact Us — CivicOS',
  '/docs':             'API Reference — Consent BB | CivicOS',
}

export default function ScrollToTop() {
  const { pathname } = useLocation()

  useEffect(() => {
    window.scrollTo({ top: 0, behavior: 'instant' })
    document.title = TITLES[pathname] ?? 'CivicOS — Open Government Building Blocks'
  }, [pathname])

  return null
}
