import { Routes, Route, Navigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { AuthAPI } from "./api/client";
import DialogCenter from "./pages/DialogCenter";
import Dashboard from "./pages/Dashboard";
import WorkflowEditor from "./pages/WorkflowEditor";
import Workflows from "./pages/Workflows";
import Agents from "./pages/Agents";
import AgentDetail from "./pages/AgentDetail";
import Groups from "./pages/Groups";
import GroupChat from "./pages/GroupChat";
import Projects from "./pages/Projects";
import ProjectDetail from "./pages/ProjectDetail";
import Runs from "./pages/Runs";
import RunDetail from "./pages/RunDetail";
import Schedules from "./pages/Schedules";
import Library from "./pages/Library";
import Workspaces from "./pages/Workspaces";
import WorkspaceDetail from "./pages/WorkspaceDetail";
import Settings from "./pages/Settings";
import Search from "./pages/Search";
import Escalations from "./pages/Escalations";
import Records from "./pages/Records";
import Automation from "./pages/Automation";
import Login from "./pages/Login";
import Layout from "./components/Layout";

export default function App() {
  const { data: me, isLoading } = useQuery({
    queryKey: ["me"],
    queryFn: AuthAPI.me,
  });

  if (isLoading) {
    return <div style={{ padding: 40, textAlign: "center", color: "var(--ink-3)" }}>Loading…</div>;
  }

  if (!me?.authenticated) {
    return (
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    );
  }

  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<Navigate to="/dialog" replace />} />
        <Route path="/dialog" element={<DialogCenter />} />
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/agents" element={<Agents />} />
        <Route path="/agents/:id" element={<AgentDetail />} />
        <Route path="/groups" element={<Groups />} />
        <Route path="/group-chat/:id" element={<GroupChat />} />
        <Route path="/projects" element={<Projects />} />
        <Route path="/projects/:id" element={<ProjectDetail />} />
        {/* New wrapper pages */}
        <Route path="/automation" element={<Automation />} />
        <Route path="/records" element={<Records />} />
        {/* Deep links and legacy direct URLs still render the standalone
           page components. The sidebar points at /automation and /records,
           but existing bookmarks / tests hitting /runs, /workflows, etc.
           continue to work unchanged. */}
        <Route path="/workflows" element={<Workflows />} />
        <Route path="/workflows/:id" element={<WorkflowEditor />} />
        <Route path="/escalations" element={<Escalations />} />
        <Route path="/runs" element={<Runs />} />
        <Route path="/runs/:id" element={<RunDetail />} />
        <Route path="/schedules" element={<Schedules />} />
        <Route path="/workspaces" element={<Workspaces />} />
        <Route path="/workspaces/:id" element={<WorkspaceDetail />} />
        <Route path="/library" element={<Library />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="/search" element={<Search />} />
      </Route>
    </Routes>
  );
}
