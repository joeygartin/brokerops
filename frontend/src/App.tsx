import { RouterProvider } from "@tanstack/react-router";
import { useState } from "react";
import { createAppRouter } from "./router";

// App mounts only once the AuthProvider has settled (BOP-025): the router is
// created here — not at module load — so its browser history reads the URL
// *after* the auth bootstrap has scrubbed any magic-link token from it. The
// shell (header + nav) and all views now live in the route tree.
export default function App() {
  const [router] = useState(createAppRouter);
  return <RouterProvider router={router} />;
}
