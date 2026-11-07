import { Route, Routes } from "react-router-dom";
import { AppShell } from "./shell/AppShell";
import { Dashboard } from "./pages/Dashboard";
import { Transactions } from "./pages/Transactions";
import { Budget } from "./pages/Budget";
import { Accounts } from "./pages/Accounts";
import { Upcoming } from "./pages/Upcoming";
import { Forecast } from "./pages/Forecast";
import { Catalogue } from "./pages/Catalogue";
import { Gifts } from "./pages/Gifts";
import { useTheme } from "./theme/useTheme";

export default function App() {
  useTheme();
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Dashboard />} />
        <Route path="transactions" element={<Transactions />} />
        <Route path="budget" element={<Budget />} />
        <Route path="accounts" element={<Accounts />} />
        <Route path="upcoming" element={<Upcoming />} />
        <Route path="forecast" element={<Forecast />} />
        <Route path="gifts" element={<Gifts />} />
        <Route path="catalogue" element={<Catalogue />} />
        <Route path="*" element={<Dashboard />} />
      </Route>
    </Routes>
  );
}
