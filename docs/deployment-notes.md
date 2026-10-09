# Food Fusion — Deployment Notes (Azure + WhatsApp go-live)

How Food Fusion went from "runs on my laptop behind ngrok" to "hosted on Azure and live on a real
WhatsApp Business number." Written as a record and as an interview story. No secrets, tokens or
account IDs are recorded here.

---

## 1. Final architecture

```
Customer / Admin phone (WhatsApp)
        │
        ▼
Meta WhatsApp Cloud API ──(HTTPS webhook, HMAC-signed)──►  Azure App Service (Linux, B1)
                                                              FastAPI + LangChain agents
                                                                 │        │         │
                                                                 ▼        ▼         ▼
                                                            Supabase   Groq API   Gemini API
                                                            Postgres   (primary)  (fallback)
                                                            (pg8000)
GitHub (main) ──GitHub Actions──► deploy to App Service
```

- **Compute:** Azure App Service, Linux, B1 plan, Central US, Python 3.13.
- **Config/secrets:** App Service application settings (injected as environment variables, read by
  `pydantic-settings`; no `.env` in production).
- **Deploy:** push to `main` → GitHub Actions installs dependencies → `azure/webapps-deploy` using
  a publish profile stored as a GitHub secret.
- **Unchanged from local:** database (Supabase, not migrated to Azure), LLM providers, schema.

## 2. Azure concepts, in the order they came up

| Concept | What it is | Where it mattered here |
|---|---|---|
| **Azure Resource Manager (ARM)** | The single API that creates, tracks and deletes every resource. Portal, CLI and IaC all call it. | Everything done with `az` could equally be done in the portal. |
| **Subscription** | Billing and quota boundary. Offer type matters (Free Trial vs Pay-As-You-Go). | A Free Trial subscription had zero compute quota and could not request more. |
| **Resource group** | Labeled container for related resources; delete it to delete everything inside. | `food-fusion-rg`. |
| **Resource provider** | Namespace backing each service (`Microsoft.Web`, `Microsoft.Compute`, …); new subscriptions don't have all registered. | `Microsoft.Web` auto-registered on first use; a "provider not registered" notice appeared on the Quotas page. |
| **App Service plan** | The compute (CPU/RAM/disk) a web app runs on; its SKU sets price and features. | B1 (Basic): always-on-capable, no free-tier sleep. |
| **Web App** | The app definition that sits on a plan; gets a permanent `*.azurewebsites.net` HTTPS URL. | `food-fusion-bot`. |
| **Quota** | Per-subscription, per-region cap on how much of a SKU you may provision. | Showed 0 for F1 and B1 in several regions. |
| **Application settings** | Key/value pairs injected as environment variables at runtime. | Replaced `.env`; secrets stay out of the repo. |
| **Startup command** | App Service doesn't auto-detect FastAPI; you give it the launch command. | `uvicorn app.main:app --host 0.0.0.0 --port 8000` (`0.0.0.0` so the platform's load balancer can reach it). |
| **Publish profile / Basic Auth** | A deploy-only credential for one app; relies on Basic Auth publishing being allowed. | Source of the "publish profile invalid" failure (see §4). |
| **Kudu (SCM site)** | The deployment engine behind App Service. | Reachable in a browser even while deployment auth was failing. |
| **GitHub Actions** | Generic runner that executes a YAML workflow on events such as a push. | `.github/workflows/azure-deploy.yml`. |
| **Service principal + OIDC federation** | Non-human identity; GitHub proves who it is with a short-lived token, so no stored secret. | The "proper" replacement for the publish profile — deferred, on the checklist. |
| **Mandatory MFA for CLI/automation** | Interactive MFA can't run in a pipeline, which is why automation uses service principals. | Surfaced as a Cloud Shell notice. |

## 3. What was done (commands, in order)

```
az group create --name food-fusion-rg --location centralus
az appservice plan create --name food-fusion-plan --resource-group food-fusion-rg --sku B1 --is-linux --location centralus
az webapp create --name food-fusion-bot --resource-group food-fusion-rg --plan food-fusion-plan --runtime "PYTHON:3.13"
az webapp config appsettings set --name food-fusion-bot --resource-group food-fusion-rg --settings KEY="value" ...
az webapp config set --name food-fusion-bot --resource-group food-fusion-rg --startup-file "uvicorn app.main:app --host 0.0.0.0 --port 8000"
```

Then: pin `requirements.txt` to exact versions and prove it in a clean venv; push the repo to GitHub;
add the publish profile as the `AZURE_WEBAPP_PUBLISH_PROFILE` secret; push to `main` to deploy;
verify `/health` and the startup logs (`az webapp log tail`); point Meta's webhook at
`https://food-fusion-bot.azurewebsites.net/webhook`; run a live order end to end.

## 4. Troubleshooting log (symptom → cause → fix)

| Symptom | Cause | Fix / outcome |
|---|---|---|
| `az appservice plan create` fails: "additional quota", current limit 0 (B1, then F1, UAE North and East US) | Free Trial subscription: compute quota withheld and not increasable ("not eligible for a quota increase") | Upgraded to Pay-As-You-Go (the trial credit carries over). |
| Still 0 quota in UAE North and East US after upgrading; IAM page said "unable to retrieve role assignments"; Quotas blade said "you don't have permissions" although the role was Owner | Newly upgraded subscription: backend state (roles, quotas) took time to propagate; per-region default allocations also differ. Exact internal cause not confirmed. | Tried a third region: **Central US worked**. |
| "Provider not registered" notice on the Quotas page | New subscription hadn't registered every resource provider | Register in Subscriptions → Resource providers if a service refuses to start; `Microsoft.Web` registered itself on first use. |
| GitHub deploy: "Publish profile is invalid for app-name and slot-name provided" | Two suspects handled: Basic Auth publishing was disabled by default on the new app (enabled it), and the profile XML copied from a terminal can be silently altered. Which one mattered was not isolated. | Enabled Basic Auth publishing, then **downloaded the profile from the portal** and pasted it into the GitHub secret → deploy succeeded. |
| `uvicorn` failed locally: DLL blocked by Windows Smart App Control (psycopg2's bundled libcrypto) | Local Windows policy; launching differently doesn't help since the block is on the DLL | Switched to the pure-Python `pg8000` driver; SSL via an explicit `SSLContext` (`CERT_NONE`, matching the previous `sslmode=require` posture). Not an Azure issue — the Linux container never had it. |
| `/health` OK, startup logs clean, `Application startup complete` | — | Confirmed the Supabase connection works from Azure (startup runs `create_all`). |
| Meta webhook verification | — | `GET /webhook` challenge returned 200 in the log stream; `messages` field subscribed. |

## 5. Going live on WhatsApp (Meta side)

1. **Live mode.** Needs a privacy policy URL; served from the app at `/privacy-policy`.
2. **Skipped Business Verification and App Review on purpose.** Not required to message real
   customers for a single business using its own app; consequence is a cap of 250 unique customer
   conversations per rolling 24 hours. Revisit when volume nears it.
3. **Dedicated number registered to the Cloud API** (Phone Number ID goes in app settings). A
   registered number stops being a normal WhatsApp account.
4. **Template recreated** under the new WhatsApp Business Account, with the customer's number added
   as a fourth parameter.
5. **Two distinct send failures worth knowing:**
   - `131047` — free-form text outside the 24-hour customer-service window. API returns 200; the
     failure arrives later in the status webhook.
   - `131042` — no billing currency / payment method on the WhatsApp Business Account. Templates
     are blocked until it is set. Free-form in-window replies still worked.
6. **Hybrid admin notification:** interactive Approve/Decline buttons while the admin's window is
   open (free-form, counts toward the monthly free allowance), template as the fallback. Pricing
   changed on 1 Oct 2026 (utility templates are charged even in-window); figures here come from
   third-party summaries — check Meta's rate card.

## 6. Cost notes

- App Service B1 was estimated at roughly **$13/month** at the time (check the portal for the live
  price). Supabase, Groq and Gemini are billed separately under their own plans.
- WhatsApp: customer replies are free-form service messages; template and over-allowance messages
  are charged per delivered message at small per-message rates. A payment method is still needed.
- Set a **budget alert** (Cost Management → Budgets) — Pay-As-You-Go has no automatic hard stop.

## 7. Hardening checklist

- [ ] **Always On** (was `false` at creation; an idle app can unload and cold-start on the next webhook)
- [ ] **HTTPS Only** (was `false`)
- [ ] Health check path configured (`/health`)
- [ ] Budget alert created
- [ ] OIDC federation + service principal instead of the publish-profile secret
- [ ] Load the Supabase CA certificate instead of `CERT_NONE`
- [ ] Review log retention (customer phone numbers appear in logs; the raw-payload debug log was removed)
- [ ] Add a WhatsApp payment method
- [ ] Rotate any credential that was ever pasted into a terminal, chat or screenshot

## 8. Interview talking points

- **Why App Service:** smallest managed option that gives a stable HTTPS endpoint, built-in secret
  injection and automatic restarts; no container or cluster to operate. Containers/Container Apps
  were deliberately out of scope until there was a reason.
- **Debugging story:** quota 0 across regions — distinguished subscription-offer limits from
  regional allocation by testing a third region; propagation delay after upgrading looked like a
  permissions problem but wasn't.
- **Security posture:** HMAC verification of every webhook, role decided by backend lookup (never
  by the LLM), secrets in app settings, known gaps listed (CERT_NONE, publish-profile auth).
- **Reliability design:** model fallback across providers, DB-grounded fallback reply, async-failure
  awareness (status webhooks), a notification path that degrades from free-form to template.
- **CV bullet (draft):** *Deployed an LLM-powered WhatsApp ordering assistant (FastAPI, LangChain,
  Groq/Gemini fallback, Supabase) to Azure App Service with GitHub Actions CI/CD, then took it live
  on the WhatsApp Cloud API with HMAC-verified webhooks, approval buttons for staff, and a
  cost-aware notification design.*
