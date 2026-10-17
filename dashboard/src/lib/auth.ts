import { useMe } from "@/api/queries";
import type { Role } from "@/api/types";

const RANK: Record<Role, number> = { viewer: 1, operator: 2, admin: 3 };

/** What the caller may do; the gateway decides, this only hides what would be refused. */
export function useCan() {
  const me = useMe();
  return (role: Role) => (me.data ? RANK[me.data.role] >= RANK[role] : false);
}
