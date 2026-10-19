import { BrowserRouter, Route, Routes } from "react-router-dom";
import { Shell } from "./components/Shell";
import { Home } from "./pages/Home";
import { Placeholder } from "./pages/Placeholder";
import { ThemeProvider } from "./lib/theme";

export default function App() {
  return (
    <ThemeProvider>
      <BrowserRouter>
        <Routes>
          <Route element={<Shell />}>
            <Route path="/" element={<Home />} />
            <Route path="/try" element={<Placeholder title="Try your data" milestone="M4" />} />
            <Route path="/:client" element={<Placeholder title="Resolve chat" milestone="M3" />} />
            <Route path="/:client/onboard" element={<Placeholder title="Onboarding" milestone="M4" />} />
            <Route path="/:client/setup" element={<Placeholder title="Setup & approvals" milestone="M4" />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </ThemeProvider>
  );
}
