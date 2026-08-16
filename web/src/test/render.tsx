import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { ChromeProvider } from '@/hooks/useChrome'
import type { ReactElement } from 'react'
import { render } from '@testing-library/react'

export function renderRoute(ui: ReactElement, path = '/n/book-1/view') {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  })
  return {
    queryClient,
    ...render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter
          initialEntries={[path]}
          future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
        >
          <ChromeProvider>
            <Routes>
              <Route path="/n/:sid/*" element={ui} />
            </Routes>
          </ChromeProvider>
        </MemoryRouter>
      </QueryClientProvider>,
    ),
  }
}
