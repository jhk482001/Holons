import { NavLink, Outlet } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { AuthAPI } from "../api/client";
import { useSyncLanguage } from "../auth";
import NotificationBell from "./NotificationBell";
import "./Layout.css";

export default function Layout() {
  const { t } = useTranslation();
  const { data: me } = useQuery({ queryKey: ["me"], queryFn: AuthAPI.me });
  useSyncLanguage();

  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="logo">
          <img src="/logo.png" alt="Holons" style={{ width: 24, height: 24, marginRight: 8 }} />
          Holons
        </div>
        <nav className="nav">
          <NavLink to="/dialog" className="nav-item">{t("nav.dialog")}</NavLink>
          <NavLink to="/dashboard" className="nav-item">{t("nav.dashboard")}</NavLink>
          <NavLink to="/agents" className="nav-item">{t("nav.agents")}</NavLink>
          <NavLink to="/groups" className="nav-item">{t("nav.teams")}</NavLink>
          <NavLink to="/projects" className="nav-item">{t("nav.projects")}</NavLink>
          <NavLink to="/automation" className="nav-item">{t("nav.automation")}</NavLink>
          <NavLink to="/records" className="nav-item">{t("nav.records")}</NavLink>
          <NavLink to="/library" className="nav-item">{t("nav.library")}</NavLink>
          <NavLink to="/settings" className="nav-item">{t("nav.settings")}</NavLink>
          <NavLink to="/search" className="nav-item">{t("nav.search")}</NavLink>
        </nav>

        <div className="sidebar-footer">
          <NotificationBell />
          <div className="user-chip">
            <div className="avatar">{me?.display_name?.[0] || "?"}</div>
            <div>
              <div className="user-name">{me?.display_name || me?.username}</div>
              <button className="logout" onClick={async () => { await AuthAPI.logout(); window.location.reload(); }}>
                {t("nav.logout")}
              </button>
            </div>
          </div>
        </div>
      </aside>
      <main className="main">
        <Outlet />
      </main>
    </div>
  );
}
