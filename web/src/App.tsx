import { BrowserRouter, Route, Routes } from "react-router-dom";
import TenantDetail from "./pages/TenantDetail";
import TenantList from "./pages/TenantList";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<TenantList />} />
        <Route path="/tenants/:id" element={<TenantDetail />} />
      </Routes>
    </BrowserRouter>
  );
}
