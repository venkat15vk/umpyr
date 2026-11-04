import { Link, NavLink, Outlet } from "react-router-dom";
import { PUBLIC_DEMO } from "../lib/api";
import { BJS_BASE, merchantName, pathFor } from "../lib/brand";
import { Logo } from "./Logo";
import { useTheme } from "../lib/theme";

export function Shell() {
  const { theme, toggle } = useTheme();
  return (
    <div className="min-h-full flex flex-col">
      <header className="sticky top-0 z-10 border-b" style={{ background: "var(--panel)", borderColor: "var(--border)" }}>
        <div className="mx-auto flex max-w-screen-2xl items-center gap-4 px-4 py-2">
          <Link to="/" className="flex items-center gap-2 font-semibold">
            <Logo />
            <span>Umpyr Resolve</span>
            <span className="badge badge-amber">POC</span>
          </Link>
          <nav className="ml-4 flex items-center gap-3 text-sm">
            <NavLink to={pathFor("rockler")} className="muted hover:underline">{merchantName("rockler")}</NavLink>
            <NavLink to={pathFor("alaska")} className="muted hover:underline">{merchantName("alaska").replace(" Airlines", "")}</NavLink>
            <NavLink to={BJS_BASE} end className="muted hover:underline">{merchantName("bjs")}</NavLink>
            <NavLink to={`${BJS_BASE}/check`} className="muted hover:underline">Check Risk &amp; Intent</NavLink>
            <NavLink to={`${BJS_BASE}/lifecycle`} className="muted hover:underline">End-to-end</NavLink>
            {!PUBLIC_DEMO && <NavLink to="/try" className="muted hover:underline">Try your data</NavLink>}
          </nav>
          <div className="ml-auto flex items-center gap-2">
            <span className="muted text-xs hidden sm:inline">All demo data is synthetic</span>
            <button className="btn" onClick={toggle} aria-label="Toggle theme">{theme === "dark" ? "Light" : "Dark"}</button>
          </div>
        </div>
      </header>
      <main className="mx-auto w-full max-w-screen-2xl flex-1 px-4 py-4">
        <Outlet />
      </main>
    </div>
  );
}
