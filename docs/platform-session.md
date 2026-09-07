# Platform sessions

Each workspace retains QuantScreen Trader's toolbar and embeds its platform with Electron's supported [WebContentsView API](https://www.electronjs.org/docs/latest/api/web-contents-view). Browser ownership, navigation and disposal remain in Electron main. The renderer never receives raw webContents or Node access. See Electron's [security guidance](https://www.electronjs.org/docs/latest/tutorial/security).

## Configuration and isolation

`apps/desktop/electron/platforms/config.ts` is the canonical browser configuration. Display names come from shared-types. Default entry points are the official [CapitalBear](https://capitalbear.com/) and [IQ Option](https://iqoption.com/) websites.

| Platform | Persistent partition |
| --- | --- |
| CapitalBear | `persist:capitalbear-profile` |
| IQ Option | `persist:iqoption-profile` |

Cookies, storage, IndexedDB and caches are independent. Browser profiles are created in Electron's OS application-data/session-data directory, never in the checkout. Normal Chromium site-session persistence is supported; the app has no password forms, password database, credential logger or automatic credential input. Moving machines or operating systems may require a fresh manual login. Do not copy cookies or browser profiles through Git.

Optional `.env` variables `QST_CAPITALBEAR_START_URL` and `QST_IQOPTION_START_URL` can choose a path on an already allowlisted origin. URLs must use HTTPS, without credentials, query values or fragments. An invalid override is rejected; adding an origin requires a reviewed source change, not a wildcard or a disabled security check.

## Navigation policy

Context isolation, sandbox and web security are enabled; Node integration is disabled. Remote views have no preload bridge. Exact-origin top-level navigation is allowed; unrelated links and all popups are rejected, not opened externally. Downloads and browser permission requests are denied. Platform subresources retain normal Chromium security and may use third-party services. No certificate override, user-agent disguise, CAPTCHA automation or anti-bot bypass is provided.

Some login/payment/help flows may require an additional legitimate origin, a popup or a browser permission and therefore be blocked. Do not weaken security to work around this; review the required flow before changing the allowlist/policy. Login and live account functionality must be verified by the user; no authenticated account was used in development tests.

## State and recovery

STARTING → LOADING → LOGIN_REQUIRED when the main document becomes available. `loadState=loaded` means the document is usable, not that authentication succeeded or that every third-party resource finished. Authentication remains unverified even after manual login; READY is defined in the contract but never inferred from a page load. A navigation failure produces ERROR or DISCONNECTED; a renderer crash produces ERROR. Reload is explicit and scoped to one platform.

Only the HTTPS origin appears in exposed URL state. Paths, query strings, fragments and raw navigation error text are omitted. Remote-page console output is not forwarded by the application. Development Chromium itself can print networking diagnostics; never publish raw runtime logs.

Both workspaces can stay open. Closing/reloading one leaves the other untouched. Browser views are disposed explicitly on close; their persistent partition is reused on reopen.
