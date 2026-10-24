import { useState } from "react";

/**
 * Contact page. A simple message form (client-side only in this build — there
 * is no backend contact endpoint) plus the team / project links. The form does
 * not transmit anything; it just confirms locally, matching the "simulated
 * only, no surprise network calls" spirit of the project.
 */
export default function Contact() {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [message, setMessage] = useState("");
  const [sent, setSent] = useState(false);

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSent(true);
  }

  return (
    <section className="page contact">
      <h1>Contact</h1>
      <p className="muted">
        Questions about GridGuard, the demo, or the project? Send a note.
      </p>

      <div className="contact-grid">
        <form className="contact-form" onSubmit={onSubmit}>
          <label htmlFor="c-name">Name</label>
          <input
            id="c-name"
            className="input"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
          />

          <label htmlFor="c-email">Email</label>
          <input
            id="c-email"
            className="input"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
          />

          <label htmlFor="c-msg">Message</label>
          <textarea
            id="c-msg"
            className="input"
            rows={5}
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            required
          />

          <button type="submit" className="btn btn-primary" disabled={sent}>
            {sent ? "Thanks — noted" : "Send"}
          </button>

          {sent && (
            <p className="muted small" role="status">
              This form is a demo and doesn't send anything over the network. Your
              message stayed in the browser.
            </p>
          )}
        </form>

        <aside className="contact-aside">
          <h2>Project</h2>
          <p>GridGuard — SJSU AI Hackathon, Oct 2026, AWS track.</p>
          <p>Built with Amazon Bedrock and Kiro.</p>
          <h2>Links</h2>
          <ul>
            <li>
              <a href="https://www.eia.gov/opendata/" target="_blank" rel="noreferrer">
                EIA Open Data
              </a>
            </li>
            <li>
              <a href="https://open-meteo.com/" target="_blank" rel="noreferrer">
                Open-Meteo
              </a>
            </li>
          </ul>
        </aside>
      </div>
    </section>
  );
}
