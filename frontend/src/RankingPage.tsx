import SiteHeader, { briefingLink } from './SiteHeader'

export default function RankingPage() {
  return <div id="top">
    <title>How stories are ranked | Court Side</title>
    <a className="skip-link" href="#ranking-process">Skip to ranking process</a>
    <SiteHeader page="ranking" />
    <main className="page-shell ranking-page" id="ranking-process">
      <section className="page-intro" aria-labelledby="page-title">
        <div>
          <p className="eyebrow intro-kicker"><span className="small-rule" /> THE METHOD</p>
          <h1 id="page-title">How stories are ranked<span>.</span></h1>
          <p className="intro-copy">The reporting behind the ranking.<br />Court Side ranks the most-covered stories among the sources we monitor.</p>
        </div>
      </section>

      <section className="ranking-section" aria-labelledby="before-scoring">
        <p className="eyebrow">BEFORE THE SCORE</p>
        <h2 id="before-scoring">Find the event. Check the coverage.</h2>
        <ol className="ranking-steps">
          <li><strong>Collect recent NBA reports.</strong><p>We collect NBA coverage for your selected date range.</p></li>
          <li><strong>Group articles about the same event.</strong><p>We compare people, dates and event details to bring related reports together.</p></li>
          <li><strong>Combine likely copies.</strong><p>Reports from the same publisher and likely copies count as one reporting group. All original links remain available.</p></li>
        </ol>
      </section>

      <section className="ranking-section" aria-labelledby="weighted-score">
        <p className="eyebrow">THE SCORE / 100</p>
        <h2 id="weighted-score">Three measures, one transparent score.</h2>
        <p>Each story’s “Why this ranks” panel shows its score breakdown.</p>
        <div className="ranking-components">
          <section><span className="ranking-weight">50<span>%</span></span><h3>Coverage</h3>
            <p>How many independent reporting groups cover the event. Eight groups earn the full 50 points.</p>
          </section>
          <section><span className="ranking-weight">30<span>%</span></span><h3>Momentum</h3>
            <p>How quickly coverage is growing compared with the earlier part of the selected period.</p>
            <p>Faster growth earns more points. Sparse coverage reduces them. Equal or falling rates earn zero.</p>
          </section>
          <section><span className="ranking-weight">20<span>%</span></span><h3>Freshness</h3>
            <p>How recently the story developed. Freshness points halve after 24 hours.</p>
            <p>New copies of an old report do not earn freshness points.</p>
          </section>
        </div>

      </section>

      <section className="ranking-section" aria-labelledby="example-score">
        <p className="eyebrow">AN ILLUSTRATION / NOT LIVE NEWS</p>
        <h2 id="example-score">What a score of 35 can mean.</h2>
        <div className="ranking-example"><table><caption>Example using the default settings and a sufficient earlier comparison period</caption>
          <thead><tr><th scope="col">Measure</th><th scope="col">Evidence</th><th scope="col">Points</th></tr></thead>
          <tbody><tr><th scope="row">Coverage</th><td>Four reporting groups</td><td>25 / 50</td></tr>
            <tr><th scope="row">Momentum</th><td>Equal recent and earlier rates</td><td>0 / 30</td></tr>
            <tr><th scope="row">Freshness</th><td>A reviewed development 24 hours old</td><td>10 / 20</td></tr></tbody>
          <tfoot><tr><th scope="row" colSpan={2}>Total score</th><td>35 / 100</td></tr></tfoot>
        </table></div>
        <p>A score measures the downloaded coverage. It is not a probability that a claim is true or a judgment of how important the event is.</p>
      </section>

      <section className="ranking-section" aria-labelledby="selection-rules">
        <p className="eyebrow">MAKING THE BRIEFING</p>
        <h2 id="selection-rules">Up to ten, when the evidence supports it.</h2>
        <p>Stories need at least two independent reporting groups to qualify. Higher scores rank first, with coverage breaking ties.</p>
        <p>When fewer stories qualify, we show fewer than ten. Developing stories appear under “On our radar.”</p>
        <p>Scores are calculated consistently. Summaries link to the reporting, and rumors remain labeled.</p>
      </section>

      <section className="ranking-section" aria-labelledby="ranking-limits">
        <p className="eyebrow">SOURCE COVERAGE</p>
        <h2 id="ranking-limits">A view of our sources.</h2>
        <p>Source coverage and update times vary. GNews free access excludes the latest 12 hours.</p>
        <p>Related reports and likely copies are grouped automatically.</p>
        <p>Unavailable sources and delayed updates are marked in the briefing.</p>
        <a className="secondary-button ranking-back" href={briefingLink()}>Back to the NBA briefing</a>
      </section>
      <footer className="site-footer"><div><strong>COURT SIDE.</strong><p>The reporting behind the order.</p></div><a href="https://github.com/awsun124/Courtside-Agent/blob/main/docs/ranking.md" target="_blank" rel="noopener noreferrer">Technical methodology</a></footer>
    </main>
  </div>
}
