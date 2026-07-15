import { createContext, useContext, useState, useCallback } from "react";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
    const [apiKey, setApiKey] = useState(
        () => localStorage.getItem("propiq_api_key") || null
    );

    const login = useCallback((key) => {
        localStorage.setItem("propiq_api_key", key);
        setApiKey(key);
    }, []);

    const logout = useCallback(() => {
        localStorage.removeItem("propiq_api_key");
        setApiKey(null);
    }, []);

    return (
        <AuthContext.Provider value={{ apiKey, isAuthed: !!apiKey, login, logout }}>
            {children}
        </AuthContext.Provider>
    );
}

export function useAuth() {
    const ctx = useContext(AuthContext);
    if (!ctx) throw new Error("useAuth must be used within AuthProvider");
    return ctx;
}