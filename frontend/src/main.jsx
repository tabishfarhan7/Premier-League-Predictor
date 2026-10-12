import React from 'react';
import ReactDOM from 'react-dom/client';
import { createBrowserRouter, RouterProvider } from 'react-router-dom';
import './index.css';

// Components
import Layout from './components/Layout';
import ErrorBoundary from './components/ErrorBoundary';

// Pages
import HomePage from './pages/HomePage';
import ContactPage from './pages/ContactPage';
import AgentPage from './pages/AgentPage';

// Error Boundary wrapper for routes
const ErrorBoundaryWrapper = ({ children }) => (
  <ErrorBoundary>{children}</ErrorBoundary>
);

// Define your routes
const router = createBrowserRouter([
  {
    path: '/',
    element: <Layout />,
    errorElement: (
      <ErrorBoundary>
        <div className="min-h-screen bg-gray-50 flex items-center justify-center px-4">
          <div className="max-w-md w-full bg-white rounded-xl shadow-lg p-8 text-center">
            <div className="text-red-500 mb-6">
              <svg className="w-16 h-16 mx-auto" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z" />
              </svg>
            </div>
            <h1 className="text-2xl font-bold text-gray-900 mb-4">Page Not Found</h1>
            <p className="text-gray-600 mb-8">The page you're looking for doesn't exist or has been moved.</p>
            <a href="/" className="bg-slate-900 text-white py-3 px-6 rounded-lg hover:bg-slate-800 transition-colors font-medium">
              Go Home
            </a>
          </div>
        </div>
      </ErrorBoundary>
    ),
    children: [
      { index: true, element: <ErrorBoundaryWrapper><HomePage /></ErrorBoundaryWrapper> },
      { path: 'agent', element: <ErrorBoundaryWrapper><AgentPage /></ErrorBoundaryWrapper> },
      { path: 'chat', element: <ErrorBoundaryWrapper><AgentPage /></ErrorBoundaryWrapper> },
      { path: 'contact', element: <ErrorBoundaryWrapper><ContactPage /></ErrorBoundaryWrapper> },
    ],
  },
]);

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <RouterProvider router={router} />
  </React.StrictMode>
);