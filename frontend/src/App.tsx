import { Route, Routes } from "react-router-dom";
import { AppShell } from "./shell/AppShell";
import { Dashboard } from "./pages/Dashboard";
import { Transactions } from "./pages/Transactions";
import { Budget } from "./pages/Budget";
import { Accounts } from "./pages/Accounts";
import { Upcoming } from "./pages/Upcoming";
import { Bills } from "./pages/Bills";
import { Forecast } from "./pages/Forecast";
import { Review } from "./pages/Review";
import { Subscriptions } from "./pages/Subscriptions";
import { Goals } from "./pages/Goals";
import { Gifts } from "./pages/Gifts";
import { Loans } from "./pages/Loans";
import { Salary } from "./pages/Salary";
import { Energy } from "./pages/Energy";
import { Reports } from "./pages/Reports";
import { Settings } from "./pages/Settings";
import { Catalogue } from "./pages/Catalogue";

export default function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Dashboard />} />
        <Route path="transactions" element={<Transactions />} />
        <Route path="budget" element={<Budget />} />
        <Route path="forecast" element={<Forecast />} />
        <Route path="review" element={<Review />} />
        <Route path="upcoming" element={<Upcoming />} />
        <Route path="bills" element={<Bills />} />
        <Route path="subscriptions" element={<Subscriptions />} />
        <Route path="goals" element={<Goals />} />
        <Route path="gifts" element={<Gifts />} />
        <Route path="loans" element={<Loans />} />
        <Route path="accounts" element={<Accounts />} />
        <Route path="salary" element={<Salary />} />
        <Route path="energy" element={<Energy />} />
        <Route path="reports" element={<Reports />} />
        <Route path="settings" element={<Settings />} />
        <Route path="catalogue" element={<Catalogue />} />
        <Route path="*" element={<Dashboard />} />
      </Route>
    </Routes>
  );
}
