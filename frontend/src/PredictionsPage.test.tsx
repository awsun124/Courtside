import { fireEvent, render, screen, within } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import PredictionsPage from './PredictionsPage'

function data() {
  return { date: '2026-11-15', status: 'available', saved_at: '2026-11-15T20:00:00Z', games: [{
    game_id: 'espn:123', home_team: 'Boston Celtics', away_team: 'Los Angeles Lakers',
    scheduled_start: '2026-11-15T23:00:00Z', cutoff_at: '2026-11-15T22:00:00Z',
    saved_at: '2026-11-15T20:00:00Z', tipoff_passed: false, model_status: 'available', model_reason: null,
    model_version: 'elo-current-season-reset-v1', home_probability: 0.6, away_probability: 0.4,
    forecast_generated_at: '2026-11-15T20:00:00Z', inputs_as_of: '2026-11-15T20:00:00Z',
    bookmakers: [{ bookmaker: 'first_book', available_at: '2026-11-15T19:00:00Z', retrieved_at: '2026-11-15T19:30:00Z',
      home_decimal_odds: 1.8, away_decimal_odds: 2.2, home_market_probability: 0.55, away_market_probability: 0.45,
      benchmark_eligible_at_save: true, stale: false, current: true, home_difference_percentage_points: 5 },
    { bookmaker: 'second_book', available_at: '2026-11-15T12:00:00Z', retrieved_at: '2026-11-15T12:30:00Z',
      home_decimal_odds: 2, away_decimal_odds: 2, home_market_probability: 0.5, away_market_probability: 0.5,
      benchmark_eligible_at_save: true, stale: true, current: false, home_difference_percentage_points: 10 }],
  }] }
}
beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(data()), { status: 200 })))
})
it('shows saved probabilities and changes bookmaker without spending a request', async () => {
  render(<PredictionsPage />)
  await screen.findByText('60.0%')
  expect(screen.getByText('+5.0 pp')).toBeTruthy()
  expect(screen.getByText('1.80')).toBeTruthy()
  fireEvent.change(screen.getByLabelText('Bookmaker'), { target: { value: 'second_book' } })
  expect(screen.getByText('+10.0 pp')).toBeTruthy()
  expect(screen.getByText(/ · Stale quote$/)).toBeTruthy()
  expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1)
  expect(vi.mocked(fetch).mock.calls[0][0]).toMatch(/^\/api\/predictions\?date=\d{4}-\d{2}-\d{2}$/)
  const nav = within(screen.getByRole('navigation', { name: 'Main navigation' }))
  expect(nav.getByRole('link', { name: 'Predictions' }).getAttribute('aria-current')).toBe('page')
})
it('requests a different saved date and handles no snapshot', async () => {
  render(<PredictionsPage />)
  await screen.findByText('60.0%')
  vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify({ status: 'no_snapshot', games: [], saved_at: null }), { status: 200 }))
  fireEvent.change(screen.getByLabelText(/Game date/), { target: { value: '2026-11-14' } })
  await screen.findByText('No predictions saved for this date.')
  expect(vi.mocked(fetch).mock.calls[1][0]).toBe('/api/predictions?date=2026-11-14')
  expect(screen.queryByText('60.0%')).toBeNull()
})
it('shows missing forecasts and markets without sample probabilities', async () => {
  const saved = data()
  Object.assign(saved.games[0], { model_status: 'unavailable', model_reason: 'One-hour prediction cutoff passed', home_probability: null, away_probability: null, bookmakers: [] })
  vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify(saved), { status: 200 }))
  render(<PredictionsPage />)
  await screen.findByText(/One-hour prediction cutoff passed/)
  expect(screen.getByText(/No matched odds were saved/)).toBeTruthy()
  expect(screen.queryByText('60.0%')).toBeNull()
})
it('shows API errors and allows a read-only retry', async () => {
  vi.mocked(fetch).mockRejectedValueOnce(new Error('Offline'))
  render(<PredictionsPage />)
  await screen.findByRole('alert')
  fireEvent.click(screen.getByRole('button', { name: 'Reload saved results' }))
  await screen.findByText('60.0%')
  expect(vi.mocked(fetch)).toHaveBeenCalledTimes(2)
})
