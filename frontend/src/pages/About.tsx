/**
 * About and limits (Screen 6 in the project plan).
 * States what GridGuard is, the "simulated only" promise, the verified facts
 * behind the demo, the data sources, and every labelled assumption. Honesty is
 * a scoring point for the hackathon ("you understand where it fails").
 */
export default function About() {
  return (
    <section className="page about">
      <h1>About and limits</h1>

      <div className="notice notice-risk" role="note">
        <span className="status status-yellow">Simulated only</span>
        <span>
          GridGuard never controls real grid equipment and never sends alerts.
          Public alerts are drafts for a human operator to review. Everything here
          runs against pre-computed scenarios.
        </span>
      </div>

      <h2>What it is</h2>
      <p>
        GridGuard helps keep the power on during California heat waves. An XGBoost
        model forecasts tomorrow's hourly demand (P10 / P50 / P90). An Amazon
        Bedrock agent drafts a plan &mdash; batteries, pre-cooling, demand response,
        and a public alert only as a last resort &mdash; tests it in a plain-code
        simulator, and revises until every hour is safe. A human operator approves,
        edits or rejects.
      </p>

      <h2>The real event (verified facts)</h2>
      <ul className="facts">
        <li>
          Sept 6, 2022: California demand hit a record 51,426 MW (EIA); CAISO's own
          figure is about 52,000 MW.
        </li>
        <li>CAISO declared Energy Emergency Alert 3 (EEA 3), the last step before rolling blackouts.</li>
        <li>
          About an hour after the peak, Cal OES sent a statewide text alert asking
          people to save power. There were no rolling blackouts.
        </li>
        <li>Grid reserves stayed around 6.2% or more of demand even at the worst moment (Cal Advocates).</li>
        <li>Evenings are the danger: it stays hot while solar fades at sunset.</li>
      </ul>

      <h2>Data sources</h2>
      <ul>
        <li>
          <a href="https://www.eia.gov/opendata/" target="_blank" rel="noreferrer">
            EIA API v2
          </a>
          : CAISO hourly demand, day-ahead forecast, and generation by fuel type.
        </li>
        <li>
          <a href="https://open-meteo.com/" target="_blank" rel="noreferrer">
            Open-Meteo
          </a>
          : hourly temperatures for LA, Riverside, Fresno, Sacramento and San Jose.
        </li>
      </ul>

      <h2>Assumptions (labelled, per our rules)</h2>
      <ul className="assumptions">
        <li>
          <span className="tag">ASSUMPTION</span> Batteries are assumed charged from
          midday solar. No recharge is modeled.
        </li>
        <li>
          <span className="tag">ASSUMPTION</span> <code>firm_mw</code> is estimated
          from EIA actuals, tuned so the no-action plan is RED around 6&ndash;7 p.m.
          at P90.
        </li>
        <li>
          <span className="tag">DEMO</span> The forecast numbers shown in this build
          are illustrative, not the trained XGBoost model or official CAISO figures.
        </li>
        <li>
          <span className="tag">DEMO</span> Security is a passcode plus rate limits.
          Production would use Cognito.
        </li>
      </ul>

      <h2>Known limits</h2>
      <ul>
        <li>The model trains on actual temperatures; real life uses forecast temperatures, so real-world error would be higher.</li>
        <li>The simulator is intentionally simple. It is a referee for the plan, not a full grid model.</li>
        <li>If the agent can't reach all-GREEN in five tries, it submits its best plan marked CANNOT_MEET_TARGET and explains the remaining risk.</li>
      </ul>

      <p className="muted small">
        Built for the SJSU AI Hackathon (Oct 2026, AWS track) with Amazon Bedrock and Kiro.
      </p>
    </section>
  );
}
