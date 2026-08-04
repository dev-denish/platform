import { useEffect, useState } from "react";

export function useCollapse(storageKey, defaultOpen = true) {
  const [open, setOpen] = useState(() => {
    const stored = sessionStorage.getItem(storageKey);
    return stored === null ? defaultOpen : stored === "1";
  });
  useEffect(() => sessionStorage.setItem(storageKey, open ? "1" : "0"), [storageKey, open]);
  return [open, () => setOpen((o) => !o)];
}
