import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { Shell } from "./components/Shell";
import { Home } from "./pages/Home";
import { Client } from "./pages/Client";
import { Try } from "./pages/Try";
import { Onboard } from "./pages/Onboard";
import { Setup } from "./pages/Setup";
import { BjsResolve } from "./pages/BjsResolve";
import { BjsCheck } from "./pages/BjsCheck";
import { BjsLifecycle } from "./pages/BjsLifecycle";
import { ThemeProvider } from "./lib/theme";
import { PUBLIC_DEMO } from "./lib/api";
import { ANON, BJS_BASE, pathFor } from "./lib/brand";

/** Anonymous build: a named BJ's link (/bjs/check) lands on its stand-in (/club/check). */
function BjsRedirect() {
  const { pathname, search } = useLocation();
  return <Navigate replace to={pathname.replace(/^\/bjs/, BJS_BASE) + search} />;
}

export default function App() {
  return (
    <ThemeProvider>
      <BrowserRouter>
        <Routes>
          <Route element={<Shell />}>
            <Route path="/" element={<Home />} />
            <Route path={BJS_BASE} element={<BjsResolve />} />
            <Route path={`${BJS_BASE}/check`} element={<BjsCheck />} />
            <Route path={`${BJS_BASE}/lifecycle`} element={<BjsLifecycle />} />
            {ANON && <Route path="/bjs/*" element={<BjsRedirect />} />}
            {ANON && <Route path="/rockler" element={<Navigate replace to={pathFor("rockler")} />} />}
            {ANON && <Route path="/alaska" element={<Navigate replace to={pathFor("alaska")} />} />}
            {!PUBLIC_DEMO && <Route path="/try" element={<Try />} />}
            <Route path="/:client" element={<Client />} />
            {!PUBLIC_DEMO && <Route path="/:client/onboard" element={<Onboard />} />}
            {!PUBLIC_DEMO && <Route path="/:client/setup" element={<Setup />} />}
          </Route>
        </Routes>
      </BrowserRouter>
    </ThemeProvider>
  );
}
