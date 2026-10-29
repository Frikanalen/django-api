# MediaMTX credentials and Django authentication

This implements source-scoped credentials and MediaMTX's HTTP authentication
callback. It does not deploy MediaMTX or configure stream paths, previews, capture,
or disconnection of active sessions.

## Issue credentials in admin

1. Create or select an enabled **Live source** owned by the organization.
2. Add a **Stream credential**, selecting that source and a label identifying the
   encoder or reader. Choose **Publish** for an encoder or **Read** for a playout
   client. Set an optional expiry.
3. Copy the generated username, password and stream path from the confirmation
   page. The password is shown only in that response and is not stored in the
   session, messages, or database. Django stores a salted password hash.

The username is the credential's key UUID. The path is exactly
`live/<source stream UUID>` (no leading slash). Configure the username/password
in the encoder using its protocol's MediaMTX credential syntax. For example:

```text
srt://<host>:8890?streamid=publish:live/<source stream UUID>:<key UUID>:<password>&pkt_size=1316
rtsp://<key UUID>:<password>@<host>:8554/live/<source stream UUID>
```

Use SRT transport encryption (configured separately) or a trusted network/VPN;
authentication itself does not encrypt the contribution. The stream UUID
identifies the source; it is not an authentication secret. A Django user's login,
API token or organization membership is not an encoder credential.

Creating a credential does not require a scheduled transmission. Contributors
must be able to connect for setup and rehearsal without putting the input on air.

To rotate, create a second credential, update the encoder, then select the old
credential in the admin list and use **Revoke selected stream credentials**.
Source and action are immutable in admin after issuance; create a new credential
rather than broadening an existing credential's scope.

Revocation and disabling a source reject subsequent authentication requests.
They do **not** disconnect already authenticated sessions. Immediate disconnection
requires the future controller to call MediaMTX's session-management API. Expiry
is also returned as `expireAt`; MediaMTX versions supporting this response field
can close a session when that instant arrives.

## Copyable connection examples

The one-time confirmation page includes selectable, read-only connection URLs
for listeners configured in the current environment. Configure their externally
reachable base addresses (no stream path, credentials, query, or fragment):

| Environment variable | Example |
| --- | --- |
| `FK_MEDIAMTX_SRT_URL` | `srt://contribute.example.org:8890` |
| `FK_MEDIAMTX_RTMP_URL` | `rtmp://contribute.example.org:1935` |
| `FK_MEDIAMTX_RTSP_URL` | `rtsp://contribute.example.org:8554` |
| `FK_MEDIAMTX_WEBRTC_URL` | `https://contribute.example.org` |
| `FK_MEDIAMTX_HLS_URL` | `https://contribute.example.org/hls` |

Only configured listeners are shown; no hostname is guessed from Django's own
address. TLS listener schemes (`rtmps`, `rtsps`, `https`) are retained. SRT, RTMP
and RTSP examples include credentials in the URL. WebRTC shows a WHIP publishing
or WHEP reading URL and a separate `username:password` bearer token for the
client's token field. Read credentials also get an HLS URL, with instructions to
use the username/password when prompted. Publish credentials do not advertise HLS.

The URLs and bearer token are shown only alongside the initial password and
are not persisted. These settings advertise listeners; they do not deploy or
configure MediaMTX, TLS, proxies or firewall rules.

## Connect MediaMTX to Django

Use MediaMTX's external HTTP authentication method:

```yaml
authMethod: http
authHTTPAddress: https://<django-host>/api/mediamtx/auth
# Do not bypass Django for privileged actions.
authHTTPExclude: []
```

Use a Django-accepted host and a network-reachable route. In the existing chart,
Django's internal service exposes the API on port 8080; the service hostname also
needs to be accepted by ALLOWED_HOSTS before using it as authHTTPAddress.
Prefer a cluster-internal route restricted to MediaMTX, or HTTPS when traversing
an untrusted network. The callback has no browser session authentication: its
purpose is to verify the encoder/reader credentials supplied by MediaMTX. It
never grants access based on a submitted IP, protocol, or a claim to be MediaMTX.
Avoid logging callback bodies or encoder URLs containing credentials.

MediaMTX sends JSON such as:

```json
{
  "user": "<credential key UUID>",
  "password": "<generated password>",
  "token": "",
  "ip": "192.0.2.1",
  "action": "publish",
  "path": "live/<source stream UUID>",
  "protocol": "srt",
  "id": "<MediaMTX connection ID>",
  "query": "",
  "userAgent": ""
}
```

Django returns:

- **204** for an allowed, non-expiring connection.
- **200** with `{"expireAt": "<RFC3339 timestamp>"}` for an allowed expiring
  credential. This is MediaMTX's exact wire spelling, independent of our public
  API's camelCase renderer.
- **401** for missing/incorrect credentials, malformed JSON, wrong paths/actions,
  revoked/expired credentials or disabled sources. Empty credentials deliberately
  return 401 so RTSP can challenge the client.
- **405** for methods other than POST.

Only `publish` and `read` are supported, each requiring a separately scoped
credential. `playback`, `api`, `metrics`, `pprof` and unknown actions are denied.
There are no anonymous reads or administrative bypasses. MediaMTX management
access, if needed, must be designed separately before enabling it. Token-only
clients are not supported by this first implementation.

This endpoint is deliberately outside DRF's session/token authentication and
camelCase parser/renderer. It accepts MediaMTX JSON with client credentials and
is CSRF-exempt only at this callback. It does not alter the authentication or
CSRF requirements of other API endpoints.

The credentials authorize connections only. The future controller must still
configure MediaMTX paths and fallback behaviour, and playout must explicitly
select an input at its booked time.

References: [MediaMTX HTTP authentication](https://mediamtx.org/docs/features/authentication)
and [SRT clients](https://mediamtx.org/docs/publish/srt).
