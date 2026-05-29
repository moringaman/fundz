declare global {
  interface Window {
    __ENV?: {
      VITE_CLERK_PUBLISHABLE_KEY?: string;
      VITE_API_URL?: string;
    };
  }
}

export {};