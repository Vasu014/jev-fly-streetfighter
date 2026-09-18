import { useEffect, useState } from "react";


const INITIAL_HEALTH = { state: "loading", payload: null };

function Dependency({ name, role, dependency }) {
  const state = dependency?.status ?? "checking";
  const ready = dependency?.ready === true;

  return (
    <article className={`fighter fighter--${name.toLowerCase()}`}>
      <div className="fighter__identity">
        <span className="fighter__role">{role}</span>
        <h2>{name}</h2>
      </div>
      <div className="fighter__telemetry">
        <span className={`signal ${ready ? "signal--ready" : ""}`} aria-hidden="true" />
        <div>
          <span className="telemetry__label">System state</span>
          <strong>{state.replaceAll("_", " ")}</strong>
        </div>
      </div>
    </article>
  );
}

export function App() {
  const [health, setHealth] = useState(INITIAL_HEALTH);

  useEffect(() => {
    const controller = new AbortController();
    fetch("/api/health", { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error(`Health returned ${response.status}`);
        return response.json();
      })
      .then((payload) => setHealth({ state: "loaded", payload }))
      .catch((error) => {
        if (error.name !== "AbortError") {
          setHealth({ state: "error", payload: null });
        }
      });
    return () => controller.abort();
  }, []);

  const dependencies = health.payload?.dependencies;

  return (
    <main>
      <header className="masthead">
        <div className="phase">Phase 1 · Systems bench</div>
        <div className="lockup" aria-label="Jev versus FlyBrain">
          <span className="lockup__jev">Jev</span>
          <span className="lockup__versus">vs</span>
          <span className="lockup__fly">FlyBrain</span>
        </div>
        <p className="intro">
          A minimal control surface for the machine mind and the neural pilot.
          No match is running yet.
        </p>
      </header>

      <section className="versus-panel" aria-label="Runtime readiness">
        <Dependency name="Jev" role="TypeSafe decision model" dependency={dependencies?.typesafe} />
        <div className="centerline" aria-hidden="true"><span>×</span></div>
        <Dependency name="FlyBrain" role="Brian2 neural runtime" dependency={dependencies?.flybrain} />
      </section>

      <section className="status-strip" aria-live="polite">
        <div>
          <span className="status-strip__label">Application link</span>
          <strong>
            {health.state === "loading" && "Checking backend…"}
            {health.state === "loaded" && "Backend online"}
            {health.state === "error" && "Backend unavailable"}
          </strong>
        </div>
        <div>
          <span className="status-strip__label">Street Fighter III</span>
          <strong>{dependencies?.mame?.status?.replaceAll("_", " ") ?? "checking"}</strong>
        </div>
        <div>
          <span className="status-strip__label">Match state</span>
          <strong>Idle by design</strong>
        </div>
      </section>

      <footer>
        Phase 1 checks configuration only. MAME, FlyBrain, and paid model calls remain stopped.
      </footer>
    </main>
  );
}
