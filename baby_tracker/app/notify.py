"""Send notifications through Home Assistant's notify services.

Uses the Supervisor core proxy (no long-lived token needed): the add-on is
given SUPERVISOR_TOKEN and `homeassistant_api: true`. No-op when no targets are
configured, so the app is silent until the user opts in.
"""
from __future__ import annotations

import logging

import httpx

log = logging.getLogger("baby.notify")

SUPERVISOR_CORE = "http://supervisor/core/api"


async def notify(cfg, title: str, message: str) -> None:
    targets = cfg.notify_targets or []
    if not targets:
        return
    if not cfg.supervisor_token:
        log.warning("notify_targets set but no SUPERVISOR_TOKEN; skipping")
        return
    headers = {"Authorization": f"Bearer {cfg.supervisor_token}"}
    body = {"title": title, "message": message}
    async with httpx.AsyncClient(timeout=10) as client:
        for target in targets:
            url = f"{SUPERVISOR_CORE}/services/notify/{target}"
            try:
                r = await client.post(url, json=body, headers=headers)
                if r.status_code >= 400:
                    log.warning("notify %s -> %s %s", target, r.status_code, r.text[:200])
            except httpx.HTTPError as e:
                log.warning("notify %s failed: %s", target, e)
