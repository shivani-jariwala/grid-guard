import { useEffect, useState } from "react";
import { Link, Navigate, Route, Routes } from "react-router-dom";
import Forecast from "./pages/Forecast";

/**
 * App routes. Single page: Forecast (Home).
 *
 * The floating nav reacts to scroll *direction*, not position (like a Smith
 * trigger with a dead zone so small jitters don't flip it):
 *   - scroll down  -> hide the nav (slide it up out of view)
 *   - scroll up    -> show the nav again
 *   - near the top -> always shown
 * It also goes compact once you've moved away from the very top.
 */
export default function App() {
  const [hidden, setHidden] = useState(false);
  const [compact, setCompact] = useState(false);

  useEffect(() => {
    let lastY = window.scrollY;
    let accum = 0; // accumulated movement in one direction
    const THRESHOLD = 14; // dead zone (px) before a direction flip takes effect
    const TOP_ZONE = 80; // always show the nav within this many px of the top

    const onScroll = () => {
      const y = window.scrollY;
      const delta = y - lastY;
      lastY = y;

      setCompact(y > 24);

      if (y <= TOP_ZONE) {
        setHidden(false);
        accum = 0;
        return;
      }

      // Reset the accumulator when direction changes, then build it up until it
      // crosses the dead zone — that's the "trigger" behaviour.
      if ((delta > 0 && accum < 0) || (delta < 0 && accum > 0)) accum = 0;
      accum += delta;

      if (accum > THRESHOLD) {
        setHidden(true); // scrolling down
        accum = 0;
      } else if (accum < -THRESHOLD) {
        setHidden(false); // scrolling up
        accum = 0;
      }
    };

    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  return (
    <div className="app">
      <header
        className={`topbar${compact ? " topbar-compact" : ""}${
          hidden ? " topbar-hidden" : ""
        }`}
      >
        <Link to="/forecast" className="brand">
          <img src="/logo.svg" alt="" className="brand-logo" />
          <span>GridGuard</span>
        </Link>
      </header>

      <main className="content">
        <Routes>
          <Route path="/" element={<Navigate to="/forecast" replace />} />
          <Route path="/forecast" element={<Forecast />} />
          <Route path="*" element={<Navigate to="/forecast" replace />} />
        </Routes>
      </main>

      <footer className="footer">
        <span>
          Simulated only. GridGuard never controls real equipment and never sends
          alerts. Alerts are drafts.
        </span>
      </footer>
    </div>
  );
}
