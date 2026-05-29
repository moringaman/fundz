declare global {
  interface Window {
    __ENV?: {
      VITE_CLERK_PUBLISHABLE_KEY?: string;
    };
  }
}

export {};