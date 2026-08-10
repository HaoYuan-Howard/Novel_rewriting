import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { AppRoutes } from './routes'
import { ChromeProvider } from './hooks/useChrome'
import './index.css'

const qc = new QueryClient({
  defaultOptions: {
    queries: {
      // 这是个要长时间盯着读的工具，读到一半整屏刷新是敌意行为
      refetchOnWindowFocus: false,
      retry: 1,
      staleTime: 5 * 60 * 1000,
    },
  },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={qc}>
      <BrowserRouter>
        <ChromeProvider>
          <AppRoutes />
        </ChromeProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
)
