from __future__ import annotations

import argparse
import urllib.parse
import urllib.request


def main() -> None:
    parser = argparse.ArgumentParser(description="Simulate a Twilio inbound SMS webhook locally.")
    parser.add_argument("--from", dest="from_number", required=True, help="Sender phone number, e.g. +15035550123")
    parser.add_argument("--to", dest="to_number", default="+15035550000", help="Twilio/business phone number")
    parser.add_argument("--body", required=True, help="SMS body")
    parser.add_argument("--sid", default="SM-local-test", help="MessageSid to send")
    parser.add_argument("--base-url", default="http://127.0.0.1:4174", help="Local app base URL")
    args = parser.parse_args()

    url = args.base_url.rstrip("/") + "/api/twilio/sms/inbound"
    payload = urllib.parse.urlencode(
        {
            "From": args.from_number,
            "To": args.to_number,
            "Body": args.body,
            "MessageSid": args.sid,
        }
    ).encode("utf-8")
    request = urllib.request.Request(url, data=payload, method="POST")
    request.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(request, timeout=15) as response:
        print(response.read().decode("utf-8"))


if __name__ == "__main__":
    main()
