# Features

One page per functional area. Pages are placeholders for now: each one is written in the same
change as the feature it describes.

When writing a page, replace *Planned content* with the actual sections and cover how it works,
configuration, error cases and tests. Remove an open point once it is settled.

## Foundations

| Page | Purpose |
| --- | --- |
| [Configuration](configuration.md) | How an installation is described without changing the code. |
| [Probe and modules](probe.md) | Runs the sensors periodically and gives them a common result format. |
| [Errors and anomalies](anomalies.md) | Reports everything that does not match what is expected, from data formats to action output. |

## Monitoring modules

| Page | Purpose |
| --- | --- |
| [Gateway](gateway.md) | Tells whether the Hermes gateway is alive and why it last stopped. |
| [Platforms](platforms.md) | Shows the connection state of Discord and the other platforms. |
| [Scheduler](scheduler.md) | Tells whether the cron scheduler still runs and succeeds. |
| [Cron jobs](cron-jobs.md) | Follows each scheduled job: runs, failures, incidents and deliveries. |
| [Kanban](kanban.md) | Follows Kanban tasks and attempts on every board. |
| [Sessions](sessions.md) | Lists the sessions of each profile with their size and cost, without any message content. |
| [Known weaknesses](weaknesses.md) | Watches the known ways Hermes can degrade silently, such as disk, memory and growing files. |

## User-facing features

| Page | Purpose |
| --- | --- |
| [Status page](status-page.md) | Gives a non-developer the state of Hermes and what to do about it. |
| [Alerts](alerts.md) | Sends one message per incident and one when it is resolved. |
| [HTTP API](api.md) | Read access to the state, and submission of prescriptions. |

## Operations and safety

| Page | Purpose |
| --- | --- |
| [Corrective actions (NURSE)](actions.md) | Runs only the actions declared in its catalogue, after checking each request. |
| [Security](security.md) | Describes the rights of each component, secrets handling and the remaining risks. |
| [Deployment and acceptance](deployment.md) | How to install, check and remove the stack next to Hermes. |
