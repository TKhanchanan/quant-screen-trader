# Platform sessions

Each workspace retains QuantScreen Trader's toolbar and embeds its platform with Electron's supported [WebContentsView API](https://www.electronjs.org/docs/latest/api/web-contents-view). Browser ownership, navigation and disposal remain in Electron main. The renderer never receives raw webContents or Node access. See Electron's [security guidance](https://www.electronjs.org/docs/latest/tutorial/security).

## Configuration and isolation

`apps/desktop/electron/platforms/config.ts` is the canonical browser configuration. Display names come from shared-types. Workspaces start directly in the [CapitalBear Traderoom](https://trade.capitalbear.com/traderoom) and [IQ Option Traderoom](https://iqoption.com/traderoom).

| Platform | Persistent partition |
| --- | --- |
| CapitalBear | `persist:capitalbear-profile` |
| IQ Option | `persist:iqoption-profile` |

Cookies, storage, IndexedDB and caches are independent. Browser profiles are created in Electron's OS application-data/session-data directory, never in the checkout. Normal Chromium site-session persistence is supported; the app has no password forms, password database, credential logger or automatic credential input. Moving machines or operating systems may require a fresh manual login. Do not copy cookies or browser profiles through Git.

Optional `.env` variables `QST_CAPITALBEAR_START_URL` and `QST_IQOPTION_START_URL` can choose a path on an already allowlisted origin. URLs must use HTTPS, without credentials, query values or fragments. An invalid override is rejected; adding an origin requires a reviewed source change, not a wildcard or a disabled security check.

## Navigation policy

Context isolation, sandbox and web security are enabled; Node integration is disabled. Remote views have no preload bridge. Top-level navigation permits the owning platform’s exact origins and `https://accounts.google.com`. Login popups use the originating workspace’s session and persistent partition, with the same navigation policy and no preload bridge. Explicit Google `redirect_uri` values must belong to the owning platform. Cross-platform destinations, unrelated origins, blank popups and nested popups are rejected; nothing is opened in a shared external browser. Closing a workspace disposes its login popups without clearing either persistent profile. Downloads and browser permission requests are denied. Platform subresources retain normal Chromium security and may use third-party services. No certificate override, user-agent disguise, CAPTCHA automation or anti-bot bypass is provided.

Some login/payment/help flows may require additional legitimate origins, blank or nested popups, or browser permissions and therefore remain blocked. Review an observed flow before changing this policy. No authenticated account was used in development tests.

Google’s [OAuth policy](https://developers.google.com/identity/protocols/oauth2/policies) prohibits authorization in embedded user-agents. Its documented [`disallowed_useragent` error](https://developers.google.com/identity/protocols/oauth2/web-server#authorization-errors) means the authorization endpoint is displayed in an embedded user-agent disallowed by that policy. This is a documented compatibility restriction, **not an observed result for either workspace**. If it occurs, stop and record the exact visible error code and message; do not disguise Electron, export cookies, or bypass the restriction. A supported alternative requires platform-provider cooperation and must preserve independent platform sessions.

## State and recovery

Blocked login navigation now produces `LOGIN_NAVIGATION_BLOCKED` with only the destination origin, without paths, queries or fragments. Login popup load failures and crashes also appear in the workspace. These errors are separate from configuration/engine errors, remain visible across page-load completion, and never imply that Google rejected authentication. An allowed new main-view navigation clears the login error; a subsequent main-view failure reports its own error.

The public platform assets inspected on 2026-09-07 establish these additional platform hops: CapitalBear’s [landing configuration](https://static.cdnroute.io/lp/capital-bear/_app/immutable/chunks/BYKdBvKI.js) links to `trade.capitalbear.com`; IQ Option’s [page initialization](https://static.cdnroute.io/_app/immutable/chunks/B-erWHCJ.js) selects `eu.iqoption.com`, `km.iqoption.com` or `sc.iqoption.com` by company, and its [authentication helper](https://static.cdnroute.io/_app/immutable/chunks/B0E375HA.js) constructs `auth.iqoption.com` login and `api.iqoption.com` token-return URLs. These exact origins are allowed only for their respective workspace. Source inspection is not an authenticated runtime verification; regional variants outside this list still require an observed destination and review.

STARTING → LOADING → UNKNOWN when the main document becomes available. `loadState=loaded` means the document is usable, not that authentication succeeded or that every third-party resource finished. Authentication remains unverified even after manual login; READY is defined in the contract but never inferred from a page load. UNKNOWN does not block visible market observation. A navigation failure produces ERROR or DISCONNECTED; a renderer crash produces ERROR. Reload is explicit and scoped to one platform.

Only the HTTPS origin appears in exposed URL state. Paths, query strings, fragments and raw navigation error text are omitted. Remote-page console output is not forwarded by the application. Development Chromium itself can print networking diagnostics; never publish raw runtime logs.

Both workspaces can stay open. Closing/reloading one leaves the other untouched. Browser views are disposed explicitly on close; their persistent partition is reused on reopen.

## Required Google sign-in acceptance verification

Status (2026-09-07): **manual verification pending for both platforms**. This development environment exposes no native apps or browser surfaces to the UI tool, and native app control is disabled. Automated tests use mocked Electron objects; they verify policy, session assignment and disposal, not Google UI usability, authenticated accounts, actual cookie persistence or restart retention.

Use the same normal application data directory throughout. Complete credentials/MFA manually in the provider UI. Do not record credentials, cookies, OAuth URLs, codes or tokens in evidence. Record app version, OS, pass/fail, and sanitized provider error text for each check.

| Step | Manual check | Result |
| --- | --- | --- |
| 1 | Open CapitalBear workspace. | Pending |
| 2 | Click Sign in with Google from CapitalBear. | Pending |
| 3 | Confirm Google UI opens and is usable in `persist:capitalbear-profile`. | Pending |
| 4 | Complete Google login manually; confirm the return authenticates CapitalBear only. | Pending |
| 5 | Close and reopen CapitalBear; confirm its account remains logged in. | Pending |
| 6 | Open IQ Option workspace. | Pending |
| 7 | Click Sign in with Google from IQ Option. | Pending |
| 8 | Confirm Google UI opens and is usable in `persist:iqoption-profile`. | Pending |
| 9 | Complete Google login manually; confirm the return authenticates IQ Option only. | Pending |
| 10 | Close and reopen IQ Option; confirm its account remains logged in. | Pending |
| 11 | Compare IQ Option’s visible account/session before and after CapitalBear login; confirm no change. | Pending |
| 12 | Compare CapitalBear’s visible account/session before and after IQ Option login; confirm no change. | Pending |
| 13 | Keep both workspaces open and confirm both remain authenticated simultaneously. | Pending |
| 14 | Quit QuantScreen Trader normally, restart with the same data directory, reopen both workspaces and confirm each retains its own account session. | Pending |

Capture the sibling workspace’s baseline before each login for steps 11–12. Judge authentication from the platform’s own account UI: the toolbar deliberately does not infer authenticated status from page loading. A provider restriction is a blocked acceptance result, not a pass; record downstream login/persistence checks as blocked when they cannot be performed.
