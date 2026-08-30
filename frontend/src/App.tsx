/**
 * App — top-level routing shell for GCSI.
 *
 * Uses hash-based routing so no server config is needed:
 *   /        → LandingPage (default)
 *   #app     → MissionControl console
 *
 * The landing page CTA sets window.location.hash = '#app'.
 * A direct link to #app jumps straight into the console.
 */
import { useState, useEffect } from 'react';
import { LandingPage } from './landing/LandingPage';
import MissionControl from './MissionControl';
import { selectSource } from './api/client';

type View = 'landing' | 'console';

function getInitialView(): View {
  if (typeof window !== 'undefined' && window.location.hash === '#app') {
    return 'console';
  }
  return 'landing';
}

export function App() {
  const [view, setView] = useState<View>(getInitialView);

  // Keep hash in sync with view
  useEffect(() => {
    const onHashChange = () => {
      setView(window.location.hash === '#app' ? 'console' : 'landing');
    };
    window.addEventListener('hashchange', onHashChange);
    return () => window.removeEventListener('hashchange', onHashChange);
  }, []);

  function launchConsole() {
    window.location.hash = '#app';
    setView('console');
  }

  /**
   * Select a specific source on the backend then open the console.
   * If the API call fails (e.g. backend not running), we still open the
   * console — the console will load whichever source is currently active.
   */
  async function launchWithSource(sourceId: string) {
    try {
      await selectSource(sourceId);
    } catch {
      // Non-fatal: console opens regardless; source selection is best-effort.
    }
    launchConsole();
  }

  if (view === 'console') {
    return <MissionControl />;
  }

  return (
    <LandingPage
      onLaunchConsole={launchConsole}
      onLaunchWithSource={launchWithSource}
    />
  );
}
