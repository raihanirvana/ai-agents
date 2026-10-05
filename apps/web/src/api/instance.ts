import { ApiClient } from "./client";

/** One client per page; its CSRF token is restored by login() or session(). */
export const api = new ApiClient();
