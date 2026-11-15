import { useEffect, useState } from 'react'
import { requestJson } from './api'
import SiteHeader from './SiteHeader'
import './predictions.css'

interface Quote {
  bookmaker: string; available_at: string; retrieved_at: string
  home_decimal_odds: number; away_decimal_odds: number
  home_market_probability: number; away_market_probability: number
  benchmark_eligible_at_save: boolean; stale: boolean; current: boolean
  home_difference_percentage_points: number | null
}
interface Game {
  game_id: string; home_team: string; away_team: string; scheduled_start: string
  cutoff_at: string; saved_at: string; tipoff_passed: boolean
  model_status: 'available' | 'unavailable'; model_reason: string | null
  model_version: string | null; forecast_generated_at: string | null; inputs_as_of: string | null
  home_probability: number | null; away_probability: number | null; bookmakers: Quote[]
}
interface Predictions {
  date: string; status: 'available' | 'no_snapshot'; saved_at: string | null; games: Game[]
}
function easternDate() {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date())
}
const time = (value: string) => new Intl.DateTimeFormat('en-US', {
  timeZone: 'America/New_York', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', timeZoneName: 'short',
}).format(new Date(value))
const percent = (value: number | null) => value === null ? '—' : `${(value * 100).toFixed(1)}%`
const difference = (value: number | null) => value === null ? '—' : `${value > 0 ? '+' : ''}${value.toFixed(1)} pp`
const bookName = (value: string) => value.replaceAll('_', ' ')

function Matchup({ game }: { game: Game }) {
  const [bookId, setBookId] = useState('')
  const book = game.bookmakers.find(row => row.bookmaker === bookId) ?? game.bookmakers.find(row => row.benchmark_eligible_at_save) ?? game.bookmakers[0]
  const delta = book?.home_difference_percentage_points ?? null
  return <article className="prediction-card" aria-labelledby={`match-${game.game_id}`}>
    <div className="prediction-card-top"><span className="eyebrow">{game.tipoff_passed ? 'SAVED PREGAME FORECAST' : 'UPCOMING MATCHUP'}</span><time dateTime={game.scheduled_start}>{time(game.scheduled_start)}</time></div>
    <h2 id={`match-${game.game_id}`}>{game.away_team}<span>at</span>{game.home_team}</h2>
    {game.home_probability !== null && <div className="prediction-bar" aria-hidden="true"><span style={{ width: `${(game.away_probability ?? 0) * 100}%` }} /><span style={{ width: `${game.home_probability * 100}%` }} /></div>}
    <div className="prediction-table-wrap"><table className="prediction-table"><caption className="sr-only">Win probabilities for {game.away_team} at {game.home_team}</caption>
      <thead><tr><th scope="col">Team</th><th scope="col">Model</th><th scope="col">Market</th><th scope="col">Difference</th></tr></thead>
      <tbody>{[
        { name: game.away_team, model: game.away_probability, market: book?.away_market_probability ?? null, delta: delta === null ? null : -delta },
        { name: game.home_team, model: game.home_probability, market: book?.home_market_probability ?? null, delta },
      ].map(side => <tr key={side.name}><th scope="row">{side.name}</th><td className="prediction-probability">{percent(side.model)}</td><td>{percent(side.market)}</td><td>{difference(side.delta)}</td></tr>)}</tbody>
    </table></div>
    {game.model_status !== 'available' && <p className="prediction-note">Model unavailable · {game.model_reason ?? 'No saved forecast for this game.'}</p>}
    {book ? <div className="prediction-market">
      <label>Bookmaker<select value={book.bookmaker} onChange={event => setBookId(event.target.value)}>{game.bookmakers.map(row => <option key={row.bookmaker} value={row.bookmaker}>{bookName(row.bookmaker)}</option>)}</select></label>
      <p>Saved decimal odds: away <strong>{book.away_decimal_odds.toFixed(2)}</strong> · home <strong>{book.home_decimal_odds.toFixed(2)}</strong></p>
      <p>Quote: {time(book.available_at)} · {game.tipoff_passed ? 'Tipoff passed' : book.stale ? 'Stale quote' : 'Saved quote'}{!book.benchmark_eligible_at_save && ' · Unavailable as a cutoff benchmark'}</p>
    </div> : <p className="prediction-note">Market unavailable · No matched odds were saved.</p>}
    <details className="prediction-details"><summary>Forecast details</summary>
      <dl><div><dt>Model</dt><dd>{game.model_version ?? 'Unavailable'}</dd></div>
        <div><dt>Saved</dt><dd>{time(game.saved_at)}</dd></div>
        <div><dt>Prediction cutoff</dt><dd>{time(game.cutoff_at)}</dd></div>
        {game.forecast_generated_at && <div><dt>Generated</dt><dd>{time(game.forecast_generated_at)}</dd></div>}
        {game.inputs_as_of && <div><dt>Inputs as of</dt><dd>{time(game.inputs_as_of)}</dd></div>}
      </dl>
    </details>
  </article>
}

export default function PredictionsPage() {
  const [day, setDay] = useState(easternDate)
  const [data, setData] = useState<Predictions | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [reload, setReload] = useState(0)
  useEffect(() => {
    if (!day) return
    const controller = new AbortController()
    setLoading(true); setError(''); setData(null)
    void requestJson<Predictions>(`/api/predictions?date=${encodeURIComponent(day)}`, { signal: controller.signal })
      .then(saved => { if (!controller.signal.aborted) setData(saved) })
      .catch(reason => { if (!controller.signal.aborted) setError(reason.message) })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [day, reload])
  const forecasts = data?.games.filter(game => game.model_status === 'available').length ?? 0
  return <div><a href="#predictions" className="skip-link">Skip to predictions</a><SiteHeader page="predictions" />
    <main className="page-shell predictions-page" id="predictions">
      <section className="page-intro"><div><p className="eyebrow intro-kicker">PREGAME / NBA</p><h1>A view of <span>the matchup.</span></h1><p className="intro-copy">Saved win probabilities, alongside the market.</p></div><div className="edition-mark"><span className="edition-label">Predictions</span><span>Experimental · Elo baseline</span></div></section>
      <div className="toolbar prediction-toolbar"><label htmlFor="prediction-date">Game date <span>(Eastern)</span><input id="prediction-date" type="date" value={day} onChange={event => { if (event.target.value) setDay(event.target.value) }} /></label>
        <button className="refresh-button" disabled={loading} onClick={() => setReload(value => value + 1)}>Reload saved results</button></div>
      {loading ? <p className="prediction-loading" role="status">Loading saved predictions…</p> : error ? <div className="notice" role="alert">{error}</div> : data?.status === 'no_snapshot' ? <section className="prediction-empty"><p className="eyebrow">NO SAVED EDITION</p><h2>No predictions saved for this date.</h2><p>Choose another date. Forecasts appear after the daily prediction job runs.</p></section> : <>
        <div className="prediction-edition" role="status"><span>{data?.games.length ?? 0} matchups · {forecasts} model forecasts</span><span>{data?.saved_at ? `Latest save ${time(data.saved_at)}` : ''}</span></div>
        {!data?.games.length ? <section className="prediction-empty"><h2>No matchups in this saved edition.</h2><p>No upcoming games were included when it was saved.</p></section> : <section className="prediction-grid" aria-label="Matchup predictions">{data.games.map(game => <Matchup key={`${day}-${game.game_id}`} game={game} />)}</section>}
      </>}
      <section className="prediction-method" aria-labelledby="prediction-method"><p className="eyebrow">READING THE NUMBERS</p><h2 id="prediction-method">A forecast, with its context.</h2><p>The Elo baseline uses current-season team results with a 24-hour delay. It has no player, injury or roster inputs. Forecasts must be saved at least one hour before tipoff.</p><p>Market probabilities remove the bookmaker’s two-sided margin. Decimal odds are the offered prices; differences are percentage points, not expected returns. Each bookmaker is shown separately.</p><p>These are saved observations. Stale quotes and forecasts after tipoff remain historical records. The first available forecast for each game is retained across daily runs. Live accuracy has not yet been evaluated.</p></section>
    </main></div>
}
