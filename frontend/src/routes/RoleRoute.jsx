import { Navigate, Outlet } from "react-router-dom";
import { useAuth } from "../context/AuthContext.jsx";

/** Renders nested routes only if the current user's role is in `allow`.
 * The API is the real enforcement point (403); this just avoids showing a
 * form the user isn't permitted to submit. */
export default function RoleRoute({ allow }) {
  const { user } = useAuth();
  if (!user || !allow.has(user.role)) {
    return <Navigate to="/" replace />;
  }
  return <Outlet />;
}
