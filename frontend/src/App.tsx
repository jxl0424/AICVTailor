import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { HealthBanner } from "./components/HealthBanner";
import { Applications } from "./tabs/Applications";
import { Changes } from "./tabs/Changes";
import { Library } from "./tabs/Library";
import { Tailor } from "./tabs/Tailor";

const TABS = [
  { to: "/tailor", label: "Tailor" },
  { to: "/changes", label: "Changes" },
  { to: "/library", label: "Library" },
  { to: "/applications", label: "Applications" },
];

export default function App() {
  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center gap-6 border-b border-ink-700 bg-ink-900 px-4">
        <span className="py-2.5 text-sm font-medium text-ink-50">AICVTailor</span>
        <nav className="flex gap-1">
          {TABS.map((tab) => (
            <NavLink
              key={tab.to}
              to={tab.to}
              className={({ isActive }) =>
                `border-b-2 px-3 py-2.5 text-sm ${
                  isActive
                    ? "border-accent text-ink-50"
                    : "border-transparent text-ink-400 hover:text-ink-200"
                }`
              }
            >
              {tab.label}
            </NavLink>
          ))}
        </nav>
        <span className="ml-auto text-xs text-ink-400">
          local only · nothing leaves this machine but resume and JD text
        </span>
      </header>

      <HealthBanner />

      <main className="flex-1 overflow-auto">
        <Routes>
          <Route path="/" element={<Navigate to="/tailor" replace />} />
          <Route path="/tailor" element={<Tailor />} />
          <Route path="/changes" element={<Changes />} />
          <Route path="/library" element={<Library />} />
          <Route path="/applications" element={<Applications />} />
        </Routes>
      </main>
    </div>
  );
}
