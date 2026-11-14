# TerraAegis — Future Health Digital Innovation Challenge

## Team
TerraAegis — 2 members, Vietnam.

- Hoang Bao Ly Tran — Huyện Quảng Ðiền, Thừa Thiên-Huế, Vietnam — @lytran14789_2231
- Thanh Minh Lê — Ho Chi Minh City, Hồ Chí Minh, Vietnam — @lethanhminh0801_4866

No institutional affiliation, sponsorship or partnership is claimed. Source organizations are cited only as data providers. Both members are 23. Ly is team captain and leads data science, modelling and AI. Minh is responsible for backend services. The team declares no relationships with challenge organizers, partners or judges.

## Challenge
[Population Health] Climate-Resilient Worker Health

## Solution summary
TerraAegis presents HealthPulse, a climate-informed public-health decision-support prototype that brings together weather, disease and population data to help planners anticipate health pressures and compare preparedness scenarios. It combines forecasting, uncertainty intervals, data provenance, resource simulations and human review. The Vietnam module covers 34 provinces and cities, with public climate and dengue data and an outbreak-planning sandbox. Provincial values after 2010 are model estimates, not reported cases.

For this challenge, we propose adapting that foundation to climate-resilient worker health. Together with workers, employers, occupational-health professionals and public-health authorities, we would identify useful local heat and exposure data, co-design planning workflows and evaluate whether the system helps teams prepare protective measures. The worker-health extension and its thresholds are proposed and require pilot validation; the existing disease models do not establish worker-heat prediction performance.

HealthPulse is designed for decision support, not diagnosis or automatic employment decisions. Data quality, uncertainty, access control and human oversight remain central. The prototype link opens the HealthPulse application and its forecasting data. It currently uses a temporary Cloudflare tunnel to a running computer, rather than durable cloud hosting.

## Solution stage
Prototype

## What has been built during the challenge?
The repository contains a working software prototype for climate-informed disease forecasting and operational preparedness. It includes public-data ingestion, point-in-time feature checks, model training and backtesting, forecast intervals, a model registry and review workflow, scenario simulation, maps and dashboards, role-based access, audit records and a grounded analyst with a rule-based fallback.

A free public preview provides guest access to the actual application, API, data and background worker. It runs on an isolated copy of the project data through a temporary Cloudflare tunnel; streaming features may be limited. The proposed workplace heat data, worker-specific risk models and protective-action workflow have not yet been built or prospectively validated.

## Technology type
- Software application
- Analytics
- Machine learning
- AI agent (the prototype includes a grounded assistant; optional LLM capabilities depend on configuration)

Do not select wearable, sensor, connected device or other hardware: none is required or claimed.

## Prototype / Deployment Link
https://filling-nirvana-topic-owner.trycloudflare.com

This is a temporary live preview of the actual service, not durable cloud hosting. It works only while the host computer and tunnel are running; the URL can change. Protected operations require authentication, and Cloudflare Quick Tunnels do not support server-sent events.
