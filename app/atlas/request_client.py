"""atlas-request: a Claude Code agent asks the owner for workbench measurements (spec §7.3).

    atlas-request "<title>" "<item>" ["<item>" ...]

The request shows up on the workbench tablet's NOTES tab as a checklist. The owner's answers land
in ATLAS's Tablet notes, never back in the agent. Standard library only, run as a file
(`python -I -S request_client.py --port P ...`) through the wrapper `integration install` writes.
Unlike the hook client this command is explicit, so it says what happened and exits non-zero on
failure: the agent should know when nothing was sent."""
import http.client
import json
import os
import sys

TIMEOUT_S = 5.0
USAGE = ('usage: atlas-request "<title>" "<item>" ["<item>" ...]\n'
         'Asks the owner, at their workbench tablet, for measurements (one per item, e.g. '
         '"ArUco ID 3 to arm base"). Their answers arrive later in ATLAS, not here.')


def main(argv):
    args, port = list(argv), 8090
    if args[:1] == ["--port"] and len(args) >= 2:
        try:
            port = int(args[1])
        except ValueError:
            print(USAGE, file=sys.stderr)
            return 2
        args = args[2:]
    if len(args) < 2 or args[0] in ("-h", "--help"):
        print(USAGE, file=sys.stderr)
        return 2
    try:
        cwd = os.getcwd()
    except OSError:
        cwd = None
    body = json.dumps({"title": args[0], "items": args[1:], "cwd": cwd}).encode("utf-8")
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=TIMEOUT_S)
    try:
        conn.request("POST", "/api/requests", body=body, headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        status, raw = resp.status, resp.read()
    except OSError:
        print("ATLAS isn't running; nothing was sent.", file=sys.stderr)
        return 1
    finally:
        conn.close()
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError:
        data = {}
    if status == 201 and isinstance(data.get("request"), dict):
        print(f"Sent to the workbench tablet ({data['request'].get('id')}). The owner's answers go to "
              "ATLAS's Tablet notes, not back to you.")
        return 0
    reason = data.get("error") if isinstance(data, dict) else None
    print(f"ATLAS didn't take it ({status}{': ' + reason if reason else ''}); nothing was sent.",
          file=sys.stderr)
    return 2 if status == 400 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
