import { useEffect } from 'react'

export function usePageMotion(location: string) {
  useEffect(() => {
    if (!window.matchMedia || !('IntersectionObserver' in window)) return
    const preference = window.matchMedia('(prefers-reduced-motion: reduce)')
    if (preference.matches) return
    const selector = '.story-card, .prediction-card, .leaderboard-card, .conference-card, .ranking-section, .prediction-research, .prediction-live, .section-heading'
    const elements = new Set<HTMLElement>()
    const observer = new IntersectionObserver(entries => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue
        const element = entry.target as HTMLElement
        element.dataset.reveal = 'visible'
        observer.unobserve(element)
      }
    }, { threshold: 0.01, rootMargin: '0px 0px -16px 0px' })
    function prepare() {
      document.querySelectorAll<HTMLElement>(selector).forEach(element => {
        if (elements.has(element)) return
        elements.add(element)
        const siblings = Array.from(element.parentElement?.children ?? []).filter(sibling => sibling.matches(selector))
        element.style.setProperty('--reveal-delay', `${Math.min(siblings.indexOf(element), 4) * 55}ms`)
        element.dataset.reveal = 'waiting'
        observer.observe(element)
      })
    }
    prepare()
    const mutations = new MutationObserver(prepare)
    mutations.observe(document.getElementById('root')!, { childList: true, subtree: true })
    function revealAll() {
      if (!preference.matches) return
      observer.disconnect()
      mutations.disconnect()
      elements.forEach(element => { delete element.dataset.reveal })
    }
    preference.addEventListener('change', revealAll)
    return () => {
      observer.disconnect()
      mutations.disconnect()
      preference.removeEventListener('change', revealAll)
      elements.forEach(element => { delete element.dataset.reveal })
    }
  }, [location])
}
