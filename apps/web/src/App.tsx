import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactElement } from 'react';
import { BrowserRouter, Route, Routes } from 'react-router-dom';

import { ErrorBoundary } from '@/components/ErrorBoundary';
import { Layout } from '@/components/Layout';
import { ProtectedRoute } from '@/components/ProtectedRoute';
import { AuthProvider } from '@/features/auth/AuthProvider';
import { ActivitiesPage } from '@/pages/ActivitiesPage';
import { ActivityDetailPage } from '@/pages/ActivityDetailPage';
import { LoginPage } from '@/pages/LoginPage';
import { NotFoundPage } from '@/pages/NotFoundPage';
import { SettingsPage } from '@/pages/SettingsPage';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Training data changes at most once an hour, when the sync cron runs
      // (spec 2.2). Refetching on every window focus would spend Supabase
      // egress — a metered free-tier resource — on data that cannot have moved.
      staleTime: 5 * 60 * 1000,
      refetchOnWindowFocus: false,
      retry: 2,
    },
  },
});

export function App(): ReactElement {
  return (
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          {/*
            basename from BASE_URL so the same build works whether it is served
            from the domain root or from /<repo>/ on GitHub Pages.
          */}
          <BrowserRouter basename={import.meta.env.BASE_URL}>
            <Routes>
              <Route path="/prihlaseni" element={<LoginPage />} />

              <Route
                element={
                  <ProtectedRoute>
                    <Layout />
                  </ProtectedRoute>
                }
              >
                <Route path="/" element={<ActivitiesPage />} />
                <Route path="/aktivita/:id" element={<ActivityDetailPage />} />
                <Route path="/nastaveni" element={<SettingsPage />} />
              </Route>

              <Route path="*" element={<NotFoundPage />} />
            </Routes>
          </BrowserRouter>
        </AuthProvider>
      </QueryClientProvider>
    </ErrorBoundary>
  );
}
