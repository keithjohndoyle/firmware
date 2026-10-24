#!/usr/bin/env python3
"""Regenerate the NordVPN server list and OpenVPN templates for the InvizBox 2 image.

Pulls NordVPN's public server list, keeps online standard servers (no Dedicated IP, which
reject ordinary accounts with AUTH_FAILED) that offer OpenVPN UDP and TCP, keeps the
least-loaded few per city, and writes:
  src/files/2/etc/config/vpn
  src/files/2/etc/openvpn/templates/nordvpn_openvpn_{udp,tcp}.ovpn.template
  src/files/2/etc/openvpn/openvpn_{1..4}.conf
"""
import json
import os
import re
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILES = os.path.join(ROOT, "src", "files", "2", "etc")
API = "https://api.nordvpn.com/v1/servers?limit=10000"
PER_CITY = 2
DNS = ("103.86.96.100", "103.86.99.100")
# vpn_1..vpn_4 defaults: (country code, preferred hostname prefix or None for the best in country)
DEFAULTS = [("IE", None), ("US", "us13298"), ("US", "us13284"), ("GB", None)]
MUST_KEEP = {"us13298", "us13284"}


def fetch():
    for attempt in range(5):
        try:
            with urllib.request.urlopen(API, timeout=120) as resp:
                return json.load(resp)
        except Exception as err:  # network flakiness on CI runners
            print("fetch failed:", err, file=sys.stderr)
            time.sleep(10 * (attempt + 1))
    sys.exit("could not download the NordVPN server list")


def online(server, identifier):
    return any(t["identifier"] == identifier and t["pivot"]["status"] == "online"
               for t in server["technologies"])


def pick(servers):
    cities = {}
    for server in servers:
        groups = {g["title"] for g in server["groups"]}
        if server["status"] != "online" or "Standard VPN servers" not in groups or "Dedicated IP" in groups:
            continue
        if not (online(server, "openvpn_udp") and online(server, "openvpn_tcp")):
            continue
        loc = server["locations"][0]["country"]
        key = (loc["code"], loc["city"]["name"])
        cities.setdefault(key, []).append(server)
    chosen = []
    for key in sorted(cities):
        ranked = sorted(cities[key], key=lambda s: s["load"])
        keep = ranked[:PER_CITY]
        keep += [s for s in ranked[PER_CITY:] if s["hostname"].split(".")[0] in MUST_KEEP]
        chosen.extend(keep)
    return chosen


def render_template(base, proto, port):
    return base.replace("@PROTO@", proto).replace("@PORT@", str(port))


def main():
    chosen = pick(fetch())
    names = {s["hostname"].split(".")[0]: s for s in chosen}
    print(len(chosen), "servers in", len({s["locations"][0]["country"]["code"] for s in chosen}), "countries")

    defaults = []
    for code, preferred in DEFAULTS:
        if preferred and preferred in names:
            defaults.append(preferred)
            continue
        pool = [n for n, s in names.items() if s["locations"][0]["country"]["code"] == code]
        if not pool:
            sys.exit("no server available for default country " + code)
        defaults.append(min(pool, key=lambda n: names[n]["load"]))

    lines = [
        "config active 'active'",
        "\toption expiry 'unknown'",
        "\toption plan ''",
        "\toption registered 'unknown'",
        "\toption renewal 'unknown'",
        "\toption provider 'nordvpn'",
    ]
    lines += ["\toption vpn_%d '%s'" % (i + 1, n) for i, n in enumerate(defaults)]
    for name in sorted(names):
        s = names[name]
        loc = s["locations"][0]["country"]
        lines += [
            "",
            "config server '%s'" % name,
            "\toption address '%s'" % s["hostname"],
            "\toption city '%s'" % loc["city"]["name"].replace("'", ""),
            "\toption country '%s'" % loc["code"],
            "\toption name '%s'" % name,
            "\tlist protocol_id 'openvpn_udp_1194'",
            "\tlist protocol_id 'openvpn_tcp_443'",
        ]
    for pid, label, default, tpl in (
            ("openvpn_udp_1194", "OpenVPN over UDP port 1194", "true", "nordvpn_openvpn_udp.ovpn.template"),
            ("openvpn_tcp_443", "OpenVPN over TCP port 443", "false", "nordvpn_openvpn_tcp.ovpn.template")):
        lines += ["", "config protocol '%s'" % pid, "\toption default '%s'" % default]
        lines += ["\tlist dns_server '%s'" % d for d in DNS]
        lines += ["\toption name '%s'" % label,
                  "\toption template '/etc/openvpn/templates/%s'" % tpl,
                  "\toption vpn_protocol 'OpenVPN'"]
    with open(os.path.join(FILES, "config", "vpn"), "w", newline="\n") as out:
        out.write("\n".join(lines) + "\n")

    with open(os.path.join(ROOT, "resources", "nordvpn_base.conf"), encoding="utf-8") as base_file:
        base = base_file.read()
    tdir = os.path.join(FILES, "openvpn", "templates")
    for stale in os.listdir(tdir):
        os.remove(os.path.join(tdir, stale))
    for proto, port, suffix in (("udp4", 1194, "udp"), ("tcp4", 443, "tcp")):
        with open(os.path.join(tdir, "nordvpn_openvpn_%s.ovpn.template" % suffix), "w", newline="\n") as out:
            out.write(render_template(base, proto, port))
    udp_base = render_template(base, "udp4", 1194)
    for i, name in enumerate(defaults, start=1):
        conf = udp_base.replace("@TUN@", "tun%d" % i).replace("@SERVER_ADDRESS@", names[name]["hostname"])
        with open(os.path.join(FILES, "openvpn", "openvpn_%d.conf" % i), "w", newline="\n") as out:
            out.write(conf)
    # the sample "my.ovpn.file" is only referenced by the placeholder vpn config
    sample = os.path.join(FILES, "openvpn", "files", "my.ovpn.file")
    if os.path.exists(sample):
        os.remove(sample)
    print("defaults:", defaults)


if __name__ == "__main__":
    main()
