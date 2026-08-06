/**
 * Mirrors app/domain/permissions.py's PERMISSION_REGISTRY - the one place a
 * grantable, per-user permission (independent of role-based RBAC, see
 * lib/roles.js) is defined. Adding a second grantable permission is a
 * registry entry here + there, never new UI code - see
 * ManagePermissionsPanel.jsx, which renders this list generically.
 */
export const PERMISSION_REGISTRY = [
  {
    name: "edit_forest_definition",
    label: "Edit forest-definition threshold",
    description:
      "Change the canopy cover, minimum height, and minimum area values that " +
      "define a forest for this platform's reports.",
  },
];
