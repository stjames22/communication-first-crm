=== Northwestern Traffic CRM ===
Contributors: northwestern-insurance-group
Tags: crm, traffic, analytics, insurance, links
Requires at least: 6.0
Tested up to: 6.6
Requires PHP: 7.4
Stable tag: 0.3.0
License: GPLv2 or later

Lightweight website traffic, easy-link tracking, and communications CRM dashboard for an insurance group.

== Description ==

Northwestern Traffic CRM is a small interim WordPress dashboard for monitoring site activity and handling simple lead communication while a fuller CRM is being built.

Features:

* 30-day traffic dashboard with visits, unique visitors, top pages, and source tracking.
* Trackable Easy Links with a reusable shortcode.
* Lead capture form shortcode.
* Simple contact records and communication timeline.
* Contact CSV export.
* Contact pipeline and recent communication dashboard panels.
* Optional sync to the Communication First CRM API for website leads, tracked clicks, and traffic events.
* Hashed IP storage and configurable traffic retention.

== Installation ==

1. Upload the `northwestern-traffic-crm` folder to `wp-content/plugins/`.
2. Activate `Northwestern Traffic CRM` in WordPress.
3. Open `Traffic CRM` in the WordPress admin sidebar.

== Shortcodes ==

Lead form:

`[nw_lead_form source="homepage"]`

Tracked Easy Link:

`[nw_easy_link key="quote-request"]`

== Notes ==

This plugin is intentionally lightweight. It does not replace a full CRM, call center system, email platform, or regulated insurance compliance workflow. Use it as an operating dashboard and handoff tool.
