import { BrowserRouter, Route, Routes } from "react-router-dom";
import { Shell } from "./components/Shell";
import { Home } from "./pages/Home";
import { Client } from "./pages/Client";
import { Try } from "./pages/Try";
import { Onboard } from "./pages/Onboard";
import { Setup } from "./pages/Setup";
import { ThemeProvider } from "./lib/theme";

export default function App() {
  return (
    <ThemeProvider>
      <BrowserRouter>
        <Routes>
          <Route element={<Shell />}>
            <Route path="/" element={<Home />} />
            <Route path="/try" element={<Try />} />
            <Route path="/:client" element={<Client />} />
            <Route path="/:client/onboard" element={<Onboard />} />
            <Route path="/:client/setup" element={<Setup />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </ThemeProvider>
  );
}
