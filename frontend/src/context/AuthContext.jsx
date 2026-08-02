import { createContext, useContext, useState, useCallback } from "react";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
    const [token, setToken] = useState(
        () => localStorage.getItem("propiq_token") || null
    );
    const [user, setUser] = useState(() => {
        const raw = localStorage.getItem("propiq_user");
        return raw ? JSON.parse(raw) : null;
    });

    const login = useCallback((newToken, newUser) => {
        localStorage.setItem("propiq_token", newToken);
        localStorage.setItem("propiq_user", JSON.stringify(newUser));
        setToken(newToken);
        setUser(newUser);
    }, []);

    const logout = useCallback(() => {
        localStorage.removeItem("propiq_token");
        localStorage.removeItem("propiq_user");
        setToken(null);
        setUser(null);
    }, []);

    return (
        <AuthContext.Provider value={{ token, user, isAuthed: !!token, login, logout }}>
            {children}
        </AuthContext.Provider>
    );
}

export function useAuth() {
    const ctx = useContext(AuthContext);
    if (!ctx) throw new Error("useAuth must be used within AuthProvider");
    return ctx;
}