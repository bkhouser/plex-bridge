# Windows startup

Use Python 3.12+ and PowerShell 7. Verify manual startup and your client connection
first. This repository does not register a scheduled task, save a Windows password
or install a Windows service automatically.

## Optional guarded tunnel launcher

`scripts/start_background.ps1` supports a specific layout: OAuth application on
loopback **8767**, tunnel health on loopback **8766**. This differs from the normal
8765 default. Set `PA_MCP_PORT=8767` and prepare the matching tunnel profile and
client paths described in [ChatGPT setup](chatgpt_setup.md).

It checks executable paths, commands, ancestry and listener ownership. Healthy
or starting instances are reused; foreign port owners, duplicates and non-loopback
bindings are refused. Anonymous HTTP 401 and matching OAuth metadata are required
before starting a missing tunnel. No unrelated process is stopped.

From the project root in PowerShell 7:

```powershell
.\tests\test_startup.ps1
.\scripts\start_background.ps1 -CheckOnly
# After preparing and verifying configuration:
.\scripts\start_background.ps1
```

The eleven synthetic ownership tests touch no services or credentials. Check-only
mode never starts missing processes. Regular mode can start missing processes
hidden, with private logs in `data/startup/`. Startup HTTP waits are bounded.

## Optional scheduled task

Use Task Scheduler for the existing user, triggered at sign-in with a short delay,
normal/limited privileges, and “Run only when user is logged on.” Use an installed
PowerShell 7 executable with arguments `-NoProfile -NonInteractive -File` followed
by the quoted absolute path to `scripts/start_background.ps1`. Set the project as
the working directory. Ignore overlapping runs and avoid a short execution limit
that terminates long-lived child processes. Use a stable PowerShell install path.

This is sign-in startup, not continuous supervision or pre-login startup. The
machine must be awake and the DPAPI-owning user signed in. Test reboot/sign-in,
Plex outage and tunnel reconnection on your deployment. Disabling the task stops
future launches, not existing processes. Never attach its private logs to issues.
