import LoadingDots from './LoadingDots'
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
interface Availability {
  status: string; coverage?: string; reason?: string; report_published_at?: string; report_url?: string
  base_home_probability: number; home_probability_change: number
  players: { name: string; status: string; team_id: string; recent_ppg: number; recent_minutes: number; estimated_margin_impact: number }[]
}
interface Game {
  result?: { home_points: number; away_points: number; correct: boolean }
  availability?: Availability
  game_id: string; home_team: string; away_team: string; scheduled_start: string
  cutoff_at: string; saved_at: string; tipoff_passed: boolean
  model_status: 'available' | 'unavailable'; model_reason: string | null
  model_version: string | null; forecast_generated_at: string | null; inputs_as_of: string | null
  home_probability: number | null; away_probability: number | null; bookmakers: Quote[]
}
interface Predictions {
  performance?: { games: number; correct: number; accuracy: number | null; pending_games: number; first_date: string | null; last_date: string | null; months: { month: string; games: number; correct: number; accuracy: number }[] }
  date: string; status: 'available' | 'no_snapshot'; saved_at: string | null; games: Game[]
}
function easternDate() {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date())
}
const time = (value: string) => new Intl.DateTimeFormat('en-US', {
  timeZone: 'America/New_York', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', timeZoneName: 'short',
}).format(new Date(value))
const percent = (value: number | null) => value === null ? '—' : `${(value * 100).toFixed(1)}%`
const difference = (value: number | null) => value === null ? '—' : `${value > 0 ? '+' : ''}${value.toFixed(1)}%`
const bookName = (value: string) => value.replaceAll('_', ' ')

function Matchup({ game }: { game: Game }) {
  const [bookId, setBookId] = useState('')
  const book = game.bookmakers.find(row => row.bookmaker === bookId) ?? game.bookmakers.find(row => row.benchmark_eligible_at_save) ?? game.bookmakers[0]
  const delta = book?.home_difference_percentage_points ?? null
  const players = game.availability?.players.filter(player => ['Out', 'Questionable', 'Doubtful'].includes(player.status)) ?? []
  const highlights = [...players].filter(player => player.estimated_margin_impact > 0).sort((a, b) => b.estimated_margin_impact - a.estimated_margin_impact).slice(0, 3)
  const missedCutoff = game.model_status !== 'available' && new Date(game.saved_at) > new Date(game.cutoff_at)
  return <article className="prediction-card" aria-labelledby={`match-${game.game_id}`}>
    <div className="prediction-card-top"><span className="eyebrow">{game.result ? 'FINAL RESULT' : game.tipoff_passed ? 'PREGAME FORECAST' : 'UPCOMING MATCHUP'}</span><time dateTime={game.scheduled_start}>{time(game.scheduled_start)}</time></div>
    <h2 id={`match-${game.game_id}`}>{game.away_team}<span>at</span>{game.home_team}</h2>
    {game.home_probability !== null && <div className="prediction-bar" aria-hidden="true"><span style={{ width: `${(game.away_probability ?? 0) * 100}%` }} /><span style={{ width: `${game.home_probability * 100}%` }} /></div>}
    <div className="prediction-table-wrap"><table className="prediction-table"><caption className="sr-only">Win probabilities for {game.away_team} at {game.home_team}</caption>
      <thead><tr><th scope="col">Team</th><th scope="col">Model</th>{book && <><th scope="col">Market</th><th scope="col">Difference</th></>}</tr></thead>
      <tbody>{[
        { name: game.away_team, model: game.away_probability, market: book?.away_market_probability ?? null, delta: delta === null ? null : -delta },
        { name: game.home_team, model: game.home_probability, market: book?.home_market_probability ?? null, delta },
      ].map(side => <tr key={side.name}><th scope="row">{side.name}</th><td className="prediction-probability">{percent(side.model)}</td>{book && <><td>{percent(side.market)}</td><td>{difference(side.delta)}</td></>}</tr>)}</tbody>
    </table></div>
    {game.result && <div className="prediction-result"><strong>Final: {game.away_team} {game.result.away_points} · {game.home_team} {game.result.home_points}</strong><span>{game.result.correct ? 'Correct winner' : 'Incorrect winner'}</span></div>}
    {game.model_status !== 'available' && <p className="prediction-note">{missedCutoff
      ? `Prediction unavailable: no forecast was saved by ${time(game.cutoff_at)}, one hour before tipoff. This snapshot was saved at ${time(game.saved_at)}, after that deadline.`
      : `Model unavailable · ${game.model_reason ?? 'No saved forecast for this game.'}`}</p>}
    {game.availability && <div className="prediction-availability">
      <p className="eyebrow">PLAYER AVAILABILITY</p>
      <p>{game.availability.status === 'applied' ? 'Confirmed absences are included in this prediction.' : game.availability.status === 'unavailable' ? 'Injury data is unavailable. This prediction uses team results only.' : 'No additional adjustment from reported absences.'} {game.availability.coverage === 'partial' && 'Player data is incomplete.'}</p>
      {highlights.length > 0 && <ul>{highlights.map(player => <li key={`${player.team_id}-${player.name}`}><strong>{player.name}</strong><span>{player.status} · Included in adjustment</span></li>)}</ul>}
      {players.length > 0 && <details className="prediction-player-list"><summary>View player availability ({players.length})</summary><ul>{players.map(player => <li key={`${player.team_id}-${player.name}`}><strong>{player.name}</strong><span>{player.status}</span></li>)}</ul></details>}

      <p className="prediction-availability-source">{game.availability.report_url && <a href={game.availability.report_url} target="_blank" rel="noreferrer">Official NBA injury report</a>}{game.availability.report_published_at && ` · ${time(game.availability.report_published_at)}`}</p>
    </div>}
    {book ? <div className="prediction-market">
      <label>Bookmaker<select value={book.bookmaker} onChange={event => setBookId(event.target.value)}>{game.bookmakers.map(row => <option key={row.bookmaker} value={row.bookmaker}>{bookName(row.bookmaker)}</option>)}</select></label>
      <p>Decimal odds: away <strong>{book.away_decimal_odds.toFixed(2)}</strong> · home <strong>{book.home_decimal_odds.toFixed(2)}</strong></p>
      <p>Quote: {time(book.available_at)} · {game.tipoff_passed ? 'Tipoff passed' : book.stale ? 'Stale quote' : 'Saved quote'}{!book.benchmark_eligible_at_save && ' · Unavailable as a cutoff benchmark'}</p>
    </div> : <p className="prediction-note">Bookmaker odds are unavailable.</p>}

  </article>
}

export default function PredictionsPage() {
  const [day, setDay] = useState(easternDate)
  const [data, setData] = useState<Predictions | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [reload, setReload] = useState(0)
  const [view, setView] = useState<'matchups' | 'results'>('matchups')
  const today = easternDate()
  function offsetDate(value: string, amount: number) {
    const next = new Date(`${value}T12:00:00Z`)
    next.setUTCDate(next.getUTCDate() + amount)
    return next.toISOString().slice(0, 10)
  }
  const earliestDay = offsetDate(today, -7)
  const latestDay = view === 'results' ? offsetDate(today, -1) : undefined
  function selectDay(value: string) {
    if (/^\d{4}-\d{2}-\d{2}$/.test(value) && value >= earliestDay && (!latestDay || value <= latestDay)) setDay(value)
  }
  function shiftDay(amount: number) { selectDay(offsetDate(day, amount)) }
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
      <section className="page-intro"><div><p className="eyebrow intro-kicker">PREGAME / NBA</p><h1>A view of <span>the matchup.</span></h1><p className="intro-copy">Saved win probabilities, alongside the market.</p></div><div className="edition-mark"><span className="edition-label">Predictions</span><span>Independent NBA prediction model</span></div></section>
      <section className="prediction-research" aria-labelledby="research-title">
        <div className="prediction-research-heading"><p className="eyebrow">THE MODEL / HISTORICAL EVALUATION</p><h2 id="research-title">Built from the game.</h2><p>An independent forecast using team results, opponent strength and game context.</p></div>
        <div className="prediction-metrics"><div><strong>74.9<span>%</span></strong><span>Accuracy on historical test games</span></div><div><strong>375 <span>/ 501</span></strong><span>Correct picks · Feb 1–Apr 12, 2026</span></div><div><strong>0.1786</strong><span>Brier score</span><span>Measures probability accuracy. Lower is better.</span></div></div>
        <p className="prediction-research-context">Our model predicted 375 of 501 game winners correctly, using team performance, player availability and official pregame injury reports. Settings were chosen using earlier games. In this historical comparison, Elo reached 74.7% accuracy and a 0.1827 Brier score.</p>
        <details className="prediction-research-details"><summary>How the model works</summary><div className="prediction-model-steps"><div><span className="eyebrow">01 / MEASURE</span><h3>Team strength</h3><p>Elo tracks wins and losses. Scoring margins and opponent-adjusted ratings measure how convincingly teams perform and who they face.</p></div><div><span className="eyebrow">02 / ADJUST</span><h3>Game context</h3><p>Home court, rest and back-to-backs help estimate the scoring margin. Recent results and longer-term strength provide different views.</p></div><div><span className="eyebrow">03 / COMBINE</span><h3>A win probability</h3><p>The model combines three estimates based on past results to predict each team’s chance of winning. Bookmaker odds are comparison data, never model inputs.</p></div></div><p>Model settings were selected using earlier games. Team ratings update with a 24-hour result delay. Official injury reports and recent player contributions adjust for confirmed absences, with smaller adjustments for long-term absences. Starting lineups are not included.</p></details>
      </section>
      <div className="prediction-view-controls" aria-label="Prediction view"><button aria-pressed={view === 'matchups'} onClick={() => { setView('matchups'); setDay(easternDate()) }}>Upcoming matchups</button><button aria-pressed={view === 'results'} onClick={() => { setView('results'); if (day >= today || day < earliestDay) setDay(offsetDate(today, -1)) }}>Past results</button></div>
      <div className="toolbar prediction-toolbar"><div className="prediction-date-controls"><button className="text-button" aria-label="Previous game date" disabled={day <= earliestDay} onClick={() => shiftDay(-1)}>←</button><label htmlFor="prediction-date">Game date <span>(Eastern)</span><input id="prediction-date" type="date" min={earliestDay} max={latestDay} value={day} onChange={event => selectDay(event.target.value)} /></label><button className="text-button" aria-label="Next game date" disabled={!!latestDay && day >= latestDay} onClick={() => shiftDay(1)}>→</button></div>
        <button className="refresh-button" disabled={loading} onClick={() => setReload(value => value + 1)}>Reload saved results</button></div>
      {loading ? <p className="prediction-loading" role="status"><LoadingDots />Loading saved predictions…</p> : error ? <div className="notice" role="alert">{error}</div> : data?.status === 'no_snapshot' ? <section className="prediction-empty"><p className="eyebrow">NO SAVED EDITION</p><h2>No predictions saved for this date.</h2><p>Try another game date.</p></section> : <>
        <div className="prediction-edition" role="status"><span>{data?.games.length ?? 0} matchups · {forecasts} model forecasts</span><span>{data?.saved_at ? `Updated ${time(data.saved_at)}` : ''}</span></div>
        {!data?.games.length ? <section className="prediction-empty"><h2>No matchups for this date.</h2><p>Check another game date.</p></section> : view === 'results' ? <PredictionResults games={data.games} /> : <section className="prediction-grid" aria-label="Matchup predictions">{data.games.map(game => <Matchup key={`${day}-${game.game_id}`} game={game} />)}</section>}
      </>}
      <section className="prediction-method" aria-labelledby="prediction-method"><p className="eyebrow">READING THE NUMBERS</p><h2 id="prediction-method">A few things to know.</h2><p>Forecasts use our NBA prediction model with confirmed injuries, recent player minutes and team results. If injury data is missing, the forecast uses the team-based model.</p><p>Market shows the bookmaker’s win probability with its margin removed. Difference compares our prediction with that probability.</p></section>
    </main></div>
}

function PredictionResults({ games }: { games: Game[] }) {
  return <section className="prediction-history" aria-label="Prediction results"><div className="stats-table-wrap"><table className="stats-table"><caption>Recorded picks and final scores</caption><thead><tr><th scope="col">Matchup</th><th scope="col">Model pick</th><th scope="col">Final score</th><th scope="col">Result</th></tr></thead><tbody>{games.map(game => {
    const available = game.model_status === 'available' && game.home_probability !== null && game.away_probability !== null
    const homePick = (game.home_probability ?? 0) >= .5
    return <tr key={game.game_id}><th scope="row">{game.away_team}<span className="history-at"> at </span>{game.home_team}</th><td>{available ? <>{homePick ? game.home_team : game.away_team}<small>{percent(homePick ? game.home_probability : game.away_probability)}</small></> : 'Unavailable'}</td><td>{game.result ? `${game.result.away_points}–${game.result.home_points}` : '—'}</td><td>{game.result ? <span className={game.result.correct ? 'history-correct' : 'history-incorrect'}>{game.result.correct ? 'Correct' : 'Incorrect'}</span> : available ? 'Pending' : 'Not graded'}</td></tr>
  })}</tbody></table></div></section>
}
