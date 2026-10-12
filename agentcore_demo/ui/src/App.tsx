import {
  BrowserRouter,
  NavLink,
  Navigate,
  Route,
  Routes,
} from "react-router-dom";
import styles from "./App.module.css";
import { useHealth, statusTone } from "./useHealth";
import OverviewTab from "./tabs/OverviewTab";
import CockpitTab from "./tabs/CockpitTab";
import TracesTab from "./tabs/TracesTab";
import GuardrailsTab from "./tabs/GuardrailsTab";
import PolicyTab from "./tabs/PolicyTab";
import RegistryTab from "./tabs/RegistryTab";
import MemoryTab from "./tabs/MemoryTab";
import EvaluationsTab from "./tabs/EvaluationsTab";

// 3-zone information architecture (ui-redesign.md): OVERVIEW (landing) /
// DEMO COCKPIT (hero) / OPERATE (the trust & quality layer: the 5 capability
// tabs). Overview is the default route.
interface NavEntry {
  path: string;
  label: string;
  /** The AgentCore capability code shown on the right (Operate zone only). */
  cap?: string;
  element: JSX.Element;
}
interface NavZone {
  label: string;
  items: NavEntry[];
}

const ZONES: NavZone[] = [
  {
    label: "Overview",
    items: [{ path: "overview", label: "Overview", element: <OverviewTab /> }],
  },
  {
    label: "Demo Cockpit",
    items: [{ path: "cockpit", label: "Demo Cockpit", element: <CockpitTab /> }],
  },
  {
    label: "Operate · Trust & Quality",
    items: [
      { path: "traces", label: "Traces", cap: "CAP-F", element: <TracesTab /> },
      { path: "evaluations", label: "Evaluations", cap: "CAP-G", element: <EvaluationsTab /> },
      { path: "registry", label: "Registry", cap: "CAP-E", element: <RegistryTab /> },
      { path: "memory", label: "Memory", cap: "CAP-C", element: <MemoryTab /> },
      { path: "guardrails", label: "Guardrails", cap: "CAP-A", element: <GuardrailsTab /> },
      { path: "policy", label: "Policy", cap: "CAP-H", element: <PolicyTab /> },
    ],
  },
];

const ALL_ENTRIES = ZONES.flatMap((z) => z.items);

const DOT_CLASS: Record<ReturnType<typeof statusTone>, string> = {
  ok: styles.dotOk,
  warn: styles.dotWarn,
  bad: styles.dotBad,
  idle: styles.dotIdle,
};

function HealthChip({ label, value }: { label: string; value?: string }) {
  const tone = statusTone(value);
  return (
    <span className={styles.healthChip} title={`${label}: ${value ?? "…"}`}>
      <span className={`${styles.healthDot} ${DOT_CLASS[tone]}`} />
      <span className={styles.healthLabel}>{label}</span>
      <span className={styles.healthValue}>{value ?? "…"}</span>
    </span>
  );
}

function TopBar() {
  const health = useHealth();
  const h = health.status === "ok" ? health.health : null;
  const loading = health.status === "loading";
  const val = (v?: string) => (loading ? undefined : v ?? "unavailable");
  return (
    <header className={styles.topbar}>
      <div className={styles.brand}>
        <span className={styles.brandMark} />
        <span className={styles.brandTitle}>Advisor Copilot</span>
        <span className={styles.brandSub}>AgentCore demo platform</span>
      </div>
      <div className={styles.healthChips}>
        <HealthChip label="Runtime" value={val(h?.runtime)} />
        <HealthChip label="Gateway" value={val(h?.gateway)} />
        <HealthChip label="Guardrail" value={val(h?.guardrail)} />
        <HealthChip label="Policy" value={val(h?.policy)} />
        <HealthChip label="Model" value={val(h?.model)} />
      </div>
    </header>
  );
}

function SideNav() {
  return (
    <nav className={styles.nav}>
      {ZONES.map((zone) => (
        <div key={zone.label} className={styles.navGroup}>
          <div className={styles.navGroupLabel}>{zone.label}</div>
          <ul className={styles.navList}>
            {zone.items.map((item) => (
              <li key={item.path}>
                <NavLink
                  to={`/${item.path}`}
                  className={({ isActive }) =>
                    `${styles.navItem} ${isActive ? styles.navItemActive : ""}`
                  }
                >
                  {item.label}
                  {item.cap && <span className={styles.navCap}>{item.cap}</span>}
                </NavLink>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </nav>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <div className={styles.app}>
        <TopBar />
        <div className={styles.body}>
          <SideNav />
          <main className={styles.main}>
            <Routes>
              <Route path="/" element={<Navigate to="/overview" replace />} />
              {ALL_ENTRIES.map((entry) => (
                <Route
                  key={entry.path}
                  path={`/${entry.path}`}
                  element={entry.element}
                />
              ))}
              <Route path="*" element={<Navigate to="/overview" replace />} />
            </Routes>
          </main>
        </div>
      </div>
    </BrowserRouter>
  );
}
