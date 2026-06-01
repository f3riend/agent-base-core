(() => {
  const COLLAPSED_KEY = "app_shell_sidebar_collapsed_v1";
  const GROUP_OPEN_KEY = "app_shell_group_open_v1";
  const GROUP_OPEN_MAP_KEY = "app_shell_group_open_map_v1";

  function readBool(key, fallback) {
    try {
      const raw = localStorage.getItem(key);
      if (raw === "1" || raw === "true") return true;
      if (raw === "0" || raw === "false") return false;
    } catch {
      /* ignore */
    }
    return fallback;
  }

  function writeBool(key, value) {
    try {
      localStorage.setItem(key, value ? "1" : "0");
    } catch {
      /* ignore */
    }
  }

  function readGroupMap() {
    try {
      const raw = localStorage.getItem(GROUP_OPEN_MAP_KEY);
      if (!raw) return {};
      const parsed = JSON.parse(raw);
      if (!parsed || typeof parsed !== "object") return {};
      return parsed;
    } catch {
      return {};
    }
  }

  function writeGroupMap(map) {
    try {
      localStorage.setItem(GROUP_OPEN_MAP_KEY, JSON.stringify(map));
    } catch {
      /* ignore */
    }
  }

  function renderIcons() {
    if (window.lucide && typeof window.lucide.createIcons === "function") {
      window.lucide.createIcons();
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    const shell = document.querySelector("[data-app-shell]");
    if (!shell) return;
    const sidebar = shell.querySelector("[data-sidebar]");
    const toggleBtn = shell.querySelector("[data-sidebar-toggle]");
    const handleBtn = shell.querySelector("[data-sidebar-handle]");
    const backdrop = shell.querySelector("[data-sidebar-backdrop]");
    const groups = Array.from(shell.querySelectorAll("[data-group]"));
    if (!sidebar || !toggleBtn || !handleBtn || !backdrop) {
      renderIcons();
      return;
    }

    const collapsed = document.documentElement.classList.contains("app-shell-collapsed")
      ? true
      : readBool(COLLAPSED_KEY, false);
    const groupOpenMap = readGroupMap();
    const legacyGroupOpen = readBool(GROUP_OPEN_KEY, true);

    const isCompactViewport = () => window.matchMedia("(max-width: 1023px)").matches;
    const applyCollapsed = (nextCollapsed, persist = true) => {
      sidebar.classList.toggle("is-collapsed", nextCollapsed);
      shell.classList.toggle("is-collapsed", nextCollapsed);
      document.documentElement.classList.toggle("app-shell-collapsed", nextCollapsed);
      handleBtn.classList.toggle("is-open", !nextCollapsed);
      handleBtn.setAttribute("aria-expanded", String(!nextCollapsed));
      toggleBtn.setAttribute("aria-expanded", String(!nextCollapsed));
      backdrop.classList.toggle("is-visible", !nextCollapsed && isCompactViewport());
      if (persist) writeBool(COLLAPSED_KEY, nextCollapsed);
    };

    applyCollapsed(collapsed, false);
    groups.forEach((group, idx) => {
      const gid = group.getAttribute("data-group-id") || `group-${idx}`;
      const fallback = gid === "settings" ? legacyGroupOpen : group.classList.contains("is-open");
      const isOpen =
        typeof groupOpenMap[gid] === "boolean" ? groupOpenMap[gid] : fallback;
      group.classList.toggle("is-open", isOpen);
      const groupToggle = group.querySelector("[data-group-toggle]");
      if (!groupToggle) return;
      groupToggle.addEventListener("click", () => {
        const next = !group.classList.contains("is-open");
        group.classList.toggle("is-open", next);
        const nextMap = { ...readGroupMap(), [gid]: next };
        writeGroupMap(nextMap);
        if (gid === "settings") writeBool(GROUP_OPEN_KEY, next);
      });
    });

    const toggleSidebar = () => {
      const next = !sidebar.classList.contains("is-collapsed");
      applyCollapsed(next);
    };

    toggleBtn.addEventListener("click", toggleSidebar);
    handleBtn.addEventListener("click", toggleSidebar);
    backdrop.addEventListener("click", () => applyCollapsed(true));
    window.addEventListener("resize", () => {
      backdrop.classList.toggle(
        "is-visible",
        !sidebar.classList.contains("is-collapsed") && isCompactViewport(),
      );
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !sidebar.classList.contains("is-collapsed")) {
        applyCollapsed(true);
      }
    });

    renderIcons();
  });
})();
