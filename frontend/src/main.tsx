import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import { Provider } from 'react-redux';
import { ClerkProvider } from '@clerk/clerk-react';
import { store } from './store';
import './index.css';
import App from './App.tsx';
import { setQueryClient } from './hooks/useQueries';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 2,
      refetchOnWindowFocus: true,
    },
  },
});

setQueryClient(queryClient);

// Read Clerk publishable key: runtime injection (Railway) first, Vite env (local dev) fallback
const PUBLISHABLE_KEY =
  window.__ENV?.VITE_CLERK_PUBLISHABLE_KEY ||
  import.meta.env.VITE_CLERK_PUBLISHABLE_KEY ||
  '';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ClerkProvider publishableKey={PUBLISHABLE_KEY}>
      <Provider store={store}>
        <BrowserRouter>
          <QueryClientProvider client={queryClient}>
            <App />
          </QueryClientProvider>
        </BrowserRouter>
      </Provider>
    </ClerkProvider>
  </StrictMode>,
);