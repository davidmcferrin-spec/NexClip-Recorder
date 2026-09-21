# ADR 0029 excerpt — standalone host + NexAPP redirect SSO

Source: `NexClip/docs/decisions/0029-standalone-host.md` (local tree).

NexClip (and NexCLIP Recorder) sit on their **own WAN host**. Cookie
`NexAPP_AUTH` is host-scoped and will not follow.

NexClip’s human SSO: `/api/v1/auth/sso/start` → NexAPP `login.php?return=`
with JWT in the query, then live `access.php`. **Recorder WAN SSO uses the
newer launch-ticket path** (`launch.php` → `POST /api/launch/redeem.php`)
from `NexAPP/examples/nexapp-launch-redeem.php` — NexAPP never connects in.

LDAP is **not coming back** on NexClip (decision 0025). Local passwords are
PBKDF2 on NexClip; Recorder keeps bcrypt local users. Optional LDAP bind on
the recorder is **standalone/air-gap only**, not hub directory sync.

NexAPP catalog admin ≠ NexClip/Recorder in-app admin. Hub role `admin` maps
to recorder `admin`; hub `user` maps to recorder `operator`.
