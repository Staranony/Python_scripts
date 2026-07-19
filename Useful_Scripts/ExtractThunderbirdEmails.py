import argparse
import csv
import mailbox
import sys
from email.header import decode_header
from email.utils import parseaddr

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_MBOX_PATHS = [ 
   #### LOCATE LOCAL FILE ADDRESS IN HERE! #### 
]

def decode_mime_header(raw):
    if not raw:

        return ""

    decoded = ""
    for text, enc in decode_header(raw):
        decoded += text.decode(enc or "utf-8", errors="replace") if isinstance(text, bytes) else text
    return decoded


def get_body(msg):
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and "attachment" not in str(part.get("Content-Disposition", "")):
               payload = part.get_payload(decode=True)
               if payload:
                    return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        return ""
    payload = msg.get_payload(decode=True)
    return payload.decode(msg.get_content_charset() or "utf-8", errors="replace") if payload else ""


def extract(mbox_path, sender):
    try:
        box = mailbox.mbox(mbox_path)
    except Exception as e:
        print(f"error: cannot open {mbox_path}: {e}", file=sys.stderr)
        return


    for msg in box:
        from_header = str(msg.get("From", ""))
        _, from_addr = parseaddr(from_header)
        if sender.lower() in from_addr.lower() or sender.lower() in from_header.lower():
            yield {
                "file": mbox_path,
                "date": msg.get("Date", ""),
                "from": from_header,
                "to": msg.get("To", ""),
                "subject": decode_mime_header(msg.get("Subject", "")),
                "body": get_body(msg),
            }


def main():
    parser = argparse.ArgumentParser(description="Filter emails by sender from Thunderbird mbox files")
    parser.add_argument("mbox", nargs="*", default=DEFAULT_MBOX_PATHS, help="one or more mbox file paths")
    parser.add_argument("--sender", required=True, help="sender address, partial match")
    parser.add_argument("--format", choices=["text", "csv"], default="text", help="output format (default: text)")
    args = parser.parse_args()


    results = []

    for path in args.mbox:
        results.extend(extract(path, args.sender))


    if not results:
        print("no matching emails found", file=sys.stderr)
        return
    if args.format == "csv":
        writer = csv.DictWriter(sys.stdout, fieldnames=["file", "date", "from", "to", "subject", "body"])
        writer.writeheader()
        writer.writerows(results)
    else:
        for m in results:
            print(f"=== {m['subject']} ===")
            print(f"From: {m['from']}")
            print(f"Date: {m['date']}")
            print(f"To: {m['to']}")
            print()
            print(m["body"])
            print("-" * 60)


if __name__ == "__main__":
    main()





    
