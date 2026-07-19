import { Routes, Route, Navigate } from "react-router-dom";
import { useAuth } from "./context/AuthContext.jsx";
import AppShell from "./components/AppShell.jsx";
import Login from "./pages/Login.jsx";
import Dashboard from "./pages/Dashboard.jsx";
import PropertyDetail from "./pages/PropertyDetail.jsx";
import ProjectBoard from "./pages/ProjectBoard.jsx";
import AdminDashboard from "./pages/AdminDashboard.jsx";

function Protected({ children }) {
    const { isAuthed } = useAuth();
    return isAuthed ? children : <Navigate to="/login" replace/>;
}

export default function App() {
    return(
        <Routes>
            <Route path="/login" element={<Login />} />
            <Route
                path="/*"
                element={
                    <Protected>
                        <AppShell>
                            <Routes>
                                <Route path="/" element={<Dashboard/> } />
                                <Route path="/properties/:id" element={<PropertyDetail />} />
                                <Route path="/projects" element={<ProjectBoard />} />
                                <Route path="/admin" element={<AdminDashboard />} />
                            </Routes>
                        </AppShell>
                    </Protected>
                }
            />
        </Routes>
    );
}