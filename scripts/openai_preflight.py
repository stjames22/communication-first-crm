from __future__ import annotations

from app.main import _openai_health_payload, openai_health_diagnostics


REASON_GUIDANCE = {
    "openai_not_configured": "Add OPENAI_API_KEY to ./backend/.env for reliable handwritten note parsing.",
    "openai_auth_failed": "The API key was rejected. Update OPENAI_API_KEY in your local environment and restart the app.",
    "openai_rate_limited": "OpenAI is reachable, but billing or rate limits are blocking requests right now.",
    "openai_model_unavailable": "The configured vision model is unavailable. Check GS_OPENAI_VISION_MODEL in ./backend/.env.",
    "openai_ca_bundle_invalid": "The configured GS_OPENAI_CA_BUNDLE file is missing or invalid. Point it at a real PEM certificate bundle.",
    "openai_dns_failed": "This Mac cannot resolve api.openai.com. Check Wi-Fi, VPN, proxy, DNS, or content-filter settings before the demo.",
    "openai_request_timed_out": "The request timed out. Check the network path, VPN, proxy, or firewall before the demo.",
    "openai_tls_failed": "The Mac reached OpenAI, but TLS/certificate validation failed. Check content filters, SSL inspection, antivirus web shields, or captive portal interception.",
    "openai_connection_refused": "The Mac reached the host, but the HTTPS connection was refused. Check firewall, security software, or network filtering.",
    "openai_connection_reset": "The HTTPS connection was reset mid-handshake. Check VPN, SSL inspection, antivirus web shields, or content filtering.",
    "openai_request_failed": "The Mac could not connect to OpenAI. Check the network path, VPN, proxy, or firewall before the demo.",
}


def main() -> int:
    payload = _openai_health_payload()
    diagnostics = openai_health_diagnostics()
    label = str(payload.get("label") or "AI Check").strip()
    detail = str(payload.get("detail") or "Unknown issue").strip()
    model = str(payload.get("model") or "gpt-4.1").strip() or "gpt-4.1"
    reason = str(payload.get("reason_code") or "").strip()

    print(f"OpenAI status: {label} - {detail} ({model})")
    print(f"DNS: {diagnostics.get('dns', {}).get('detail', 'Unknown')}")
    print(f"TCP: {diagnostics.get('tcp', {}).get('detail', 'Unknown')}")
    print(f"HTTP: {diagnostics.get('http', {}).get('detail', 'Unknown')}")
    print(f"Proxy/VPN env: {diagnostics.get('proxy', {}).get('detail', 'Unknown')}")
    print(f"SSL mode: {'Demo override on (certificate checks skipped)' if diagnostics.get('insecure_ssl') else 'Normal certificate validation'}")
    if diagnostics.get("ca_bundle"):
        print(f"CA bundle: {diagnostics.get('ca_bundle')}")

    guidance = REASON_GUIDANCE.get(reason)
    if guidance:
        print(f"Next step: {guidance}")
    if reason == "openai_tls_failed" and not diagnostics.get("insecure_ssl"):
        print("Permanent fix option: export the trusted root certificate to PEM and set GS_OPENAI_CA_BUNDLE=/full/path/to/cert.pem in ./backend/.env.")
        print("Local demo fallback: set GS_OPENAI_ALLOW_INSECURE_SSL=1 in ./backend/.env only if you trust this network.")

    if reason and reason != "ok":
        print("Demo fallback: use the Measurement Review paste box or add manual rows if handwritten note parsing is unavailable.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
