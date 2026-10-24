# Soybean Admin integration

Source: https://github.com/soybeanjs/soybean-admin/tree/v2.3.0

Pinned commit: `ab40c08992807e303dd8aabb6ec43d7e2334b24f`.

The complete `src/store/modules/theme/`, `src/layouts/modules/theme-drawer/`,
`src/theme/settings.ts`, `vars.ts` and all four preset JSON files are migrated.
All 20 drawer files, token definitions and presets are unchanged upstream source.
Application defaults select vertical-mix with a 64px rail (48px icon-only when collapsed) and 180px child menu;
the source manifest records this customization and verifies the migrated files.
The store retains every upstream setting/action, including automatic dark mode,
auxiliary color modes, semantic palettes, recommended colors and watermark controls.
Two small integration fixes in its index keep CSS tokens updated when a preset
changes only tokens and clear preset Naive overrides on reset.

The original color algorithm and Uno preset are in `packages/`; layout, PageTab,
and SimpleScrollbar come from `packages/materials`. `transition.css` is unchanged.
The existing AdminLayout adds the upstream base-layout calculations and all six
upstream menu modules. The menu context/route adapter uses this application's static
route hierarchy rather than Elegant Router's underscore naming convention.
Vertical-mix uses the authenticated device catalog for its dark rail and the existing
business menu in its child panel; mobile uses the native vertical menu and device selector.
The project logo, cookie sessions, device scoping, business pages and report caches
are retained. The app/auth adapters do not introduce template authentication or APIs.
The theme store's production-only cache behavior is retained; configuration is saved
before refresh/close. Valid preferences from the prior drawer are migrated once.

Companion components explicitly register Naive components in place of upstream
unplugin-components. SVG icons are bundled offline from Iconify; no icon CDN is used.
The required locale namespaces are copied from upstream. Language switching covers
the theme UI and Naive controls; business page translations are outside this port.
The tab adapter uses upstream PageTab's three styles and honors visibility, height,
persistence and middle-click settings while retaining the existing report KeepAlive.
Tab identity uses the upstream helper: query-only filter changes update an existing tab.

The upstream MIT license is retained in `LICENSE`; production builds copy notices for
all direct runtime dependencies and this vendored source into `dist/licenses/`.
