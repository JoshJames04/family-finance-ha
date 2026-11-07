import { Route, Routes } from "react-router-dom";
import { AppShell } from "./shell/AppShell";
import { Dashboard } from "./pages/Dashboard";
import { Transactions } from "./pages/Transactions";
import { Budget } from "./pages/Budget";
import { Accounts } from "./pages/Accounts";
import { Upcoming } from "./pages/Upcoming";
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
        <Route path="*" element={<Dashboard />} />
      </Route>
    </Routes>
  );
}
