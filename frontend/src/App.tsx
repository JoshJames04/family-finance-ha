import { useEffect, useState } from "react";
import { Route, Routes } from "react-router-dom";

function Dashboard() {
  const [version, setVersion] = useState<string>("…");
  useEffect(() => {
    fetch("api/v1/version")
      .then((r) => r.json())
      .then((b) => setVersion(b.version))
      .catch(() => setVersion("unreachable"));
  }, []);
  return (
    <main style={{ fontFamily: "system-ui", padding: "2rem" }}>
      <h1>Family Finance</h1>
      <p>Dev shell running. API version: {version}</p>
    </main>
  );
}

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Dashboard />} />
      <Route path="*" element={<Dashboard />} />
    </Routes>
  );
}
