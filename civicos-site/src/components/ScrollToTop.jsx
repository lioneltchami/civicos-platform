import { useEffect } from 'react'
import { useLocation } from 'react-router-dom'

const TITLES = {
  '/': 'CivicOS — GovStack CMS Submission Website',
  '/building-blocks': 'Building blocks roadmap — CivicOS',
  '/about': 'About CivicOS',
  '/contact': 'Submission contact — CivicOS',
  '/docs': 'Content Management System submission docs — CivicOS',
}

export default function ScrollToTop() {
  const { pathname } = useLocation()

  useEffect(() => {
    window.scrollTo({ top: 0, behavior: 'instant' })
    document.title = TITLES[pathname] ?? 'CivicOS — GovStack CMS Submission Website'
  }, [pathname])

  return null
}
