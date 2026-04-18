import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { AuthAPI, MeResponse } from "./api/client";

/**
 * Shared React Query hook for the current user. Re-uses the same `["me"]`
 * query that App.tsx bootstraps, so no extra network call.
 */
export function useMe() {
  return useQuery<MeResponse>({
    queryKey: ["me"],
    queryFn: AuthAPI.me,
  });
}

export function useIsAdmin(): boolean {
  const { data } = useMe();
  return data?.role === "admin";
}

/**
 * Syncs the i18n language from the user's profile. Call this once near the
 * app root (e.g., in App.tsx or Layout.tsx). When the user changes language
 * in Settings, the `["me"]` query is invalidated and this hook picks up
 * the new value.
 */
export function useSyncLanguage() {
  const { data } = useMe();
  const { i18n } = useTranslation();
  useEffect(() => {
    const lang = (data as any)?.language;
    if (lang && lang !== i18n.language) {
      i18n.changeLanguage(lang);
    }
  }, [(data as any)?.language]);
}
